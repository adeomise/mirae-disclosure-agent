"""유형만 밝힌 거래소공시 조회의 실측 코퍼스 통합 테스트.

무엇을 고정하는가:
    질의 해석이 거래소공시 경로를 열었다는 것만으로는 이 변경의 목적이 이뤄지지 않는다.
    그 계획이 실제 코퍼스에서 문서 조회 단계까지 도달하고, 후보를 하나로 좁히지 못했을 때
    하나를 고르지 않는 것까지 확인해야 한다. 계획 단계는
    tests/unit/test_supply_contract_lookup_query_plan.py가 본다.

    그래서 여기서 보는 것은 셋이다.

        1. 막힌 자리가 query_plan이 아니다   실행이 문서 조회까지 갔다는 사실이다. 예전에는
                                            질의 해석에서 unsupported_metric으로 끝나
                                            코퍼스를 한 번도 열지 않았다.
        2. 후보가 여럿이면 고르지 않는다      ambiguous_root로 막고 답을 만들지 않는다.
        3. 후보가 없으면 없다고 답한다        다른 기간·다른 기업의 공시로 대신하지 않는다.

무엇을 고정하지 않는가:
    자연어 답변을 만들지 않는다. 이 경로의 범위는 AgentExecutionResult까지다.

    상대방으로 후보를 좁히는 능력(C3)은 여전히 없다. 계획이 담는 counterparty는 조회 조건이
    아니라 fail-close 신호이며, 그 성질이 이 변경으로 바뀌지 않는 것을 아래에서 함께 본다.

실제 공시 값에 대하여:
    접수번호와 수치를 여기에 적지 않는다. 후보 개수처럼 코퍼스가 정하는 값은 이 파일이
    직접 세어 비교한다. 코퍼스가 없으면 건너뛴다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_ROOT,
    STAGE_FILING_DISCOVERY,
    STAGE_QUERY_PLAN,
)
from disclosure_agent.agent.router import answer_question
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    ExecutionStatus,
)
from disclosure_agent.common.schemas import AgentExecutionResult

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"

DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)

SUPPLY_CONTRACT_SUBTYPE = "단일판매공급계약체결"

# 이번 변경이 열어야 하는 두 질문.
TARGET_PLAIN = "삼성전자 2024년 공급계약 알려줘"
TARGET_WITH_COUNTERPARTY = "삼성전자 2024년 Ford 관련 공급계약 알려줘"

# 후보가 여럿인 기업으로 같은 모양을 다시 묻는다. 하나를 고르지 않는 것을 보려면 고를 것이
# 여럿이어야 하고, 삼성전자는 이 코퍼스의 2024년에 공급계약 공시가 없다.
AMBIGUOUS_PLAIN = "LG에너지솔루션 2024년 공급계약 알려줘"
AMBIGUOUS_WITH_COUNTERPARTY = "LG에너지솔루션 2024년 Ford 관련 공급계약 알려줘"


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


def _stage(result: AgentExecutionResult) -> str | None:
    """실행이 멈춘 자리. 막히지 않았으면 None."""
    if result.execution_issue is not None:
        return result.execution_issue.stage

    detail = result.answerability_detail

    return detail.stage if detail is not None else None


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
# 1. 질의 해석에서 끝나지 않는다
# --------------------------------------------------------------------------


@pytest.mark.corpus
@pytest.mark.parametrize(
    "question",
    [TARGET_PLAIN, TARGET_WITH_COUNTERPARTY, AMBIGUOUS_PLAIN],
    ids=["plain", "with-counterparty", "ambiguous"],
)
def test_lookup_questions_reach_document_discovery(
    corpus_root: Path,
    question: str,
) -> None:
    """
    막힌 자리가 query_plan이 아니라 filing_discovery인지 본다.

    이것이 이 변경의 핵심 사실이다. 예전에는 세 질문 모두 query_plan에서
    unsupported_metric으로 끝났고, 코퍼스를 한 번도 열지 않았다. 사유만 보면 '지표를 모르겠다'
    였으나 질문은 지표를 묻지 않았다.

    답이 나오는지는 여기서 보지 않는다. 후보 개수가 정할 일이고 그것은 아래에서 따로 본다.
    """
    result = _ask(corpus_root, question)

    assert _stage(result) != STAGE_QUERY_PLAN
    assert _stage(result) == STAGE_FILING_DISCOVERY
    # 계획 단계에서 멈춘 것이 아니므로 실행 중단이 아니다.
    assert result.execution_issue is None
    assert result.execution_status is ExecutionStatus.COMPLETED


# --------------------------------------------------------------------------
# 2. 후보가 여럿이면 고르지 않는다
# --------------------------------------------------------------------------


@pytest.mark.corpus
@pytest.mark.parametrize(
    "question",
    [AMBIGUOUS_PLAIN, AMBIGUOUS_WITH_COUNTERPARTY],
    ids=["plain", "with-counterparty"],
)
def test_multiple_candidates_are_refused_without_choosing_one(
    corpus_root: Path,
    question: str,
) -> None:
    """
    후보가 여럿인 질문에서 하나를 고르지 않는지 본다.

    상대방을 적은 질문도 같은 판정에 걸린다. 이 구현은 상대방으로 후보를 좁히지 못하므로
    (C3 미구현), 이름을 적었다는 이유로 후보가 줄어들면 안 된다. 두 질문이 같은 사유로
    막히는 것이 그 사실을 드러낸다.

    Fact도 Evidence도 만들지 않는다. 대상 문서를 확정하지 못한 채로 값을 담으면, 어느 계약의
    값인지 모르는 숫자가 근거까지 갖춘 답으로 나간다.
    """
    expected = _original_filing_count(corpus_root, "LG에너지솔루션", 2024)

    # 대조군이 성립하는지 먼저 확인한다. 후보가 1건 이하면 이 테스트는 아무것도 보지 못한다.
    assert expected > 1

    result = _ask(corpus_root, question)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_FILING_DISCOVERY
    assert detail.reason == REASON_AMBIGUOUS_ROOT
    # 몇 건 사이에서 막혔는지가 진단에 남아야 질문을 어떻게 좁힐지 알 수 있다.
    assert str(expected) in detail.detail

    assert result.facts == []
    assert result.evidence == []


# --------------------------------------------------------------------------
# 3. 후보가 없으면 없다고 답한다
# --------------------------------------------------------------------------


@pytest.mark.corpus
@pytest.mark.parametrize(
    "question",
    [TARGET_PLAIN, TARGET_WITH_COUNTERPARTY],
    ids=["plain", "with-counterparty"],
)
def test_absent_candidates_are_reported_as_not_found(
    corpus_root: Path,
    question: str,
) -> None:
    """
    그 기간에 그 기업의 공급계약 원공시가 없으면 없다고 답하는지 본다.

    삼성전자는 이 코퍼스의 2024년에 공급계약 공시가 없다. 다른 기간이나 다른 기업의 공시로
    대신하지 않는 것이 여기서 볼 사실이며, 답을 만들지 못한 것과 답이 없다는 것을 구분해
    보고하는 것이 그 다음이다.

    UNANSWERABLE은 실행이 끝난 뒤의 판정이다. 코퍼스를 열어 보고 없다는 것을 확인했으므로
    실행 중단(BLOCKED)이 아니다.
    """
    # 대조군이 성립하는지 먼저 확인한다.
    assert _original_filing_count(corpus_root, "삼성전자", 2024) == 0

    result = _ask(corpus_root, question)

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND
    assert _stage(result) == STAGE_FILING_DISCOVERY

    assert result.facts == []
    assert result.evidence == []


# --------------------------------------------------------------------------
# 4. 이번 범위 밖의 경계
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_counterparty_only_question_still_stops_at_query_plan(corpus_root: Path) -> None:
    """
    대상 기업 없이 상대방만 적은 질문은 그대로 막힌다.

    "2024년 Ford 관련 공급계약 알려줘"는 코퍼스 전체를 상대방으로 뒤지는 조회다. 그 경로는
    어느 계층에도 없으며(retrieval의 조회 진입점이 전부 company를 필수로 받는다), 이 변경이
    그 경계를 옮기지 않는다.

    실측 코퍼스로 다시 보는 이유:
        단위 테스트는 인식 후보 목록을 직접 넘긴다. 실제 manifest에 Ford가 corp_name으로
        들어와 있었다면 그 목록만으로는 드러나지 않는다. 여기서는 코퍼스가 아는 이름 전체를
        가지고도 같은 판정이 나오는 것을 본다.
    """
    result = _ask(corpus_root, "2024년 Ford 관련 공급계약 알려줘")

    assert result.execution_issue is not None
    assert result.execution_issue.stage == STAGE_QUERY_PLAN
    assert result.execution_issue.reason == "unknown_company"
