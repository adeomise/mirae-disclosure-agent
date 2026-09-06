"""공급계약 공시 내용 조회 질문의 실측 코퍼스·HTTP 통합 테스트.

무엇을 고정하는가:
    질의 해석이 거절한다는 것만으로는 이 변경의 목적이 이뤄지지 않는다. 그 거절이 HTTP
    경계까지 같은 사유로 나가고, 코퍼스를 열어 답을 만들어 내지 않는 것까지 확인해야 한다.
    계획 단계의 세부는 tests/unit/test_supply_contract_lookup_query_plan.py가 본다.

    그래서 여기서 보는 것은 넷이다.

        1. 실측 코퍼스에서도 거절한다        원공시가 1건으로 확정되는 기업·연도로 묻는다.
                                            후보가 없어서 막히는 것이 아니라는 뜻이다.
        2. Fact도 Evidence도 만들지 않는다   답을 만들다 만 것이 아니라 만들지 않았다.
        3. HTTP 422로 나간다                실행이 끝나지 못했으므로 200이 아니다.
        4. 이력 질문은 그대로 실행된다        거절이 Pattern E를 함께 막지 않았다.

    1번의 대조군 성격이 중요하다. 삼성전자는 이 코퍼스의 2025년에 공급계약 원공시가 정확히
    1건 있고 정정이 1건 붙어 있다. 예전 구현은 그래서 이 질문을 COMPLETED/ANSWERABLE로
    끝냈고, 돌아온 문장은 계약의 내용이 아니라 "계약상대 변경 전 …, 변경 후 …"였다. 답이
    나오는 조건을 그대로 두고 답하지 않기로 한 것이므로, 후보가 있는 자리에서 확인해야 한다.

무엇을 고정하지 않는가:
    자연어 답변의 문장을 고정하지 않는다. 거절된 질문에는 답 문장이 없고, 이력 질문의 문장은
    이 PR이 만들지 않았다.

실제 공시 값에 대하여:
    접수번호와 수치를 여기에 적지 않는다. 후보 개수처럼 코퍼스가 정하는 값은 이 파일이 직접
    세어 비교한다. 코퍼스가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.agent.answer_builder import STAGE_QUERY_PLAN
from disclosure_agent.agent.router import answer_question
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityStatus, ExecutionStatus
from disclosure_agent.common.schemas import AgentExecutionResult

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"

DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)

SUPPLY_CONTRACT_SUBTYPE = "단일판매공급계약체결"

REASON_UNSUPPORTED_CONTRACT_FIELDS = "unsupported_contract_fields"

# #141 리뷰가 지목한 다섯 질문. 삼성전자 2025년은 원공시가 1건으로 확정되는 자리다.
TYPE_ONLY_REQUEST = "삼성전자 2025년 공급계약 알려줘"

REFUSED_REQUESTS = (
    TYPE_ONLY_REQUEST,
    "삼성전자 2025년 공급계약 상대방 알려줘",
    "삼성전자 2025년 공급계약 계약명 알려줘",
    "삼성전자 2025년 공급계약 공급지역 알려줘",
    "삼성전자 2025년 공급계약 주요 내용 알려줘",
    "삼성전자 2025년 Ford 관련 공급계약 알려줘",
)

REFUSED_IDS = [
    "type-only",
    "counterparty",
    "contract-name",
    "supply-region",
    "main-content",
    "ford-relation",
]

# 이력 질문. 이 거절이 Pattern E를 함께 막지 않았음을 보이는 대조군이다.
HISTORY_REQUEST = "LG에너지솔루션이 포드랑 맺었던 배터리 공급계약, 지금 어떻게 됐어?"

client = TestClient(app)


def _resolve_corpus_root() -> Path | None:
    configured = os.environ.get(CORPUS_ROOT_ENV)

    root = Path(configured) if configured else DEFAULT_CORPUS_ROOT

    return root if (root / "manifest.jsonl").exists() else None


@pytest.fixture(scope="module")
def corpus_root() -> Path:
    root = _resolve_corpus_root()

    if root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    return root


def _ask(corpus_root: Path, question: str) -> AgentExecutionResult:
    return answer_question(question, corpus_root=corpus_root)


def _original_filing_count(corpus_root: Path, company: str, year: int) -> int:
    """그 해에 제출된 그 기업의 공급계약 원공시 수. manifest에서 직접 센다."""
    count = 0

    with (corpus_root / "manifest.jsonl").open(encoding="utf-8") as handle:
        for line in handle:
            record = json.loads(line)

            if record.get("corp_name") != company:
                continue

            if record.get("doc_subtype") != SUPPLY_CONTRACT_SUBTYPE:
                continue

            if record.get("is_correction"):
                continue

            if str(record.get("rcept_dt", "")).startswith(str(year)):
                count += 1

    return count


# --------------------------------------------------------------------------
# 0. 대조군이 성립하는지 먼저 확인한다
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_the_refused_question_has_exactly_one_original_filing(corpus_root: Path) -> None:
    """
    거절을 확인할 자리에 실제로 후보가 있는지 먼저 본다.

    후보가 0건이면 아래 테스트들은 '없어서 못 답했다'로도 통과한다. 그러면 이 PR이 무엇을
    바꿨는지 아무것도 고정하지 못한다. 답이 나올 수 있는 조건에서 답하지 않기로 한 것이므로
    후보가 정확히 1건인 자리를 쓴다.
    """
    assert _original_filing_count(corpus_root, "삼성전자", 2025) == 1


# --------------------------------------------------------------------------
# 1. 실측 코퍼스에서도 거절한다
# --------------------------------------------------------------------------


@pytest.mark.corpus
@pytest.mark.parametrize("question", REFUSED_REQUESTS, ids=REFUSED_IDS)
def test_content_requests_are_refused_at_query_plan(
    corpus_root: Path,
    question: str,
) -> None:
    """
    다섯 질문이 계획 단계에서 멈추고 코퍼스를 열지 않는지 본다.

    막힌 자리가 query_plan이라는 것이 '검색에 이르지 않았다'의 실질이다. 계약 내용을 답할
    carrier가 없으므로 문서를 여는 것 자체가 의미 없고, 열면 그 문서로 무언가를 답하고 싶어진다.
    """
    result = _ask(corpus_root, question)

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.execution_issue is not None
    assert result.execution_issue.stage == STAGE_QUERY_PLAN
    assert result.execution_issue.reason == REASON_UNSUPPORTED_CONTRACT_FIELDS


@pytest.mark.corpus
@pytest.mark.parametrize("question", REFUSED_REQUESTS, ids=REFUSED_IDS)
def test_refused_requests_produce_no_answer_payload(
    corpus_root: Path,
    question: str,
) -> None:
    """
    거절한 질문에 Fact도 Evidence도 이력도 만들지 않는지 본다.

    예전 구현은 이 자리에서 EventHistory 1건과 Evidence 3건을 만들어 ANSWERABLE로 돌려줬다.
    그 결과는 형식이 완전해서 물어본 것에 답하지 못했다는 사실이 드러나지 않았다.
    """
    result = _ask(corpus_root, question)

    assert result.facts == []
    assert result.evidence == []
    assert result.event_histories == []
    assert result.event_links == []
    assert result.answerability_status is not AnswerabilityStatus.ANSWERABLE


@pytest.mark.corpus
def test_refusal_does_not_use_the_metric_vocabulary(corpus_root: Path) -> None:
    """
    막힌 사유가 unsupported_metric이 아닌지 실행 결과에서 본다.

    #141 리뷰가 명시적으로 요구한 조건이며, 질의 해석 단위 테스트와 따로 둔다. 사유를 실제로
    사용자에게 내보내는 것은 이 경로이기 때문이다.
    """
    result = _ask(corpus_root, TYPE_ONLY_REQUEST)

    assert result.execution_issue is not None
    assert result.execution_issue.reason != "unsupported_metric"


# --------------------------------------------------------------------------
# 2. HTTP 경계
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_refused_request_returns_422_with_the_same_reason() -> None:
    """
    거절이 HTTP 경계까지 같은 사유로 나가는지 본다.

    422인 것이 규약이다. 실행이 끝나고 나서 답을 확정하지 못한 것(200/UNRESOLVED)이 아니라
    실행이 시작되지 못한 것이다. 두 경우를 같은 상태 코드로 내보내면 받는 쪽이 되물어 풀 수
    있는 질문과 아직 만들지 않은 기능을 구분할 수 없다.
    """
    if _resolve_corpus_root() is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    response = client.post("/api/ask", json={"question": TYPE_ONLY_REQUEST})

    assert response.status_code == 422

    body = response.json()

    assert body["execution_status"] == ExecutionStatus.BLOCKED.value
    assert body["error"]["stage"] == STAGE_QUERY_PLAN
    assert body["error"]["reason"] == REASON_UNSUPPORTED_CONTRACT_FIELDS


@pytest.mark.corpus
def test_history_question_still_reaches_the_event_carrier() -> None:
    """
    이력 질문이 예전 그대로 실행되는지 HTTP 경계에서 본다.

    이 거절이 Pattern E를 함께 막지 않았다는 대조군이다. Q14는 원공시 후보가 여럿이라
    UNRESOLVED로 끝나며, 그것은 실행이 끝난 뒤의 판정이므로 200이다.
    """
    if _resolve_corpus_root() is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    response = client.post("/api/ask", json={"question": HISTORY_REQUEST})

    assert response.status_code == 200

    body = response.json()

    assert body["status"] == AnswerabilityStatus.UNRESOLVED.value


# --------------------------------------------------------------------------
# 3. 이번 범위 밖의 경계
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_counterparty_only_question_still_stops_on_the_company_reason(
    corpus_root: Path,
) -> None:
    """
    대상 기업 없이 상대방만 적은 질문은 기업을 읽지 못한 사유로 막힌다.

    "2025년 Ford 관련 공급계약 알려줘"는 코퍼스 전체를 상대방으로 뒤지는 조회이며, 그 경로는
    어느 계층에도 없다. 기업 판정이 계약 필드 판정보다 훨씬 앞이므로 사유도 그쪽이다.

    실측 코퍼스로 보는 이유:
        단위 테스트는 인식 후보 목록을 직접 넘긴다. 실제 manifest에 Ford가 corp_name으로
        들어와 있었다면 그 목록만으로는 드러나지 않는다.
    """
    result = _ask(corpus_root, "2025년 Ford 관련 공급계약 알려줘")

    assert result.execution_issue is not None
    assert result.execution_issue.stage == STAGE_QUERY_PLAN
    assert result.execution_issue.reason == "unknown_company"
