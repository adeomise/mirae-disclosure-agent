"""실제 코퍼스로 실행하는 Q02 공급계약 단건 조회 HTTP 회귀.

검증 대상:
- 질문 문자열 하나가 /api/ask를 지나 근거 있는 계약 답변으로 나오는가
- 확정된 공시가 질문이 가리킨 그 계약인가
- Fact의 값이 원문 공시의 값과 같은가
- 각 Fact의 근거가 응답 안에서 실제로 풀리는가

단위 테스트와 나누는 이유:
    아래 계층은 각자의 테스트가 이미 고정하고 있다. 여기서 보는 것은 그 계층들이 실제 코퍼스
    위에서 이어지는지이며, 합성 fixture로는 문서 경로 해석·본문 표기처럼 실제 파일에서만
    드러나는 어긋남을 잡지 못한다.

코퍼스가 없으면 건너뛴다. 저장소에 실제 공시 파일을 두지 않기 때문이다(AGENTS.md).
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityStatus, MetricConcept
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

# 질문이 가리키는 계약. 이 값들은 실제 공시 원문에서 온다.
EXPECTED_DOC_ID = "exchange_20250917800008"

EXPECTED_AMOUNT = "1933600000000"

EXPECTED_AMOUNT_UNIT = "KRW"

EXPECTED_START_DATE = "2025-09-16"

EXPECTED_END_DATE = "2029-05-31"

# 공시 본문이 계약 상대방을 적은 표기.
#
# 질문을 이 표기로 쓰는 이유:
#     원문은 상대방을 한글 상호로 적는다. 라틴 표기("Yang Ming")로 물으면 현재 구현은
#     찾지 못하고 막는다(아래 회귀 참조). 두 표기를 잇는 일은 상대방 대조 규칙을 넓히는
#     결정이고, 이 파일이 정할 사항이 아니다.
COUNTERPARTY = "양밍해운"

Q02 = f"한화오션이 2025년 9월 {COUNTERPARTY}과 체결한 계약의 계약금액과 계약기간은?"

# 같은 계약을 라틴 표기로 물은 질문.
Q02_LATIN = "한화오션이 2025년 9월 Yang Ming과 체결한 계약의 계약금액과 계약기간은?"

client = TestClient(app)


def _require_actual_corpus() -> None:
    raw_corpus_root = os.environ.get(CORPUS_ROOT_ENV)
    if not raw_corpus_root:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")

    corpus_root = Path(raw_corpus_root).expanduser()
    if not corpus_root.is_dir():
        pytest.skip(f"corpus root is not a directory: {corpus_root}")

    raw_manifest = os.environ.get(CORPUS_MANIFEST_ENV)
    manifest_path = (
        Path(raw_manifest).expanduser()
        if raw_manifest is not None
        else corpus_root / "manifest.jsonl"
    )
    if not manifest_path.is_file():
        pytest.skip(f"corpus manifest does not exist: {manifest_path}")


@pytest.mark.corpus
def test_q02_supply_contract_answer_is_grounded_in_the_actual_filing() -> None:
    """
    대표 Q02가 실제 공시 1건에서 세 값을 모두 확보해 답이 되는지 끝까지 본다.

    값·근거·문장을 함께 보는 이유:
        셋 중 하나만 보면 나머지가 어긋나도 통과한다. 문장만 보면 근거 없는 문장이, 값만 보면
        값과 다른 문장이 지나간다.
    """
    _require_actual_corpus()

    response = client.post("/api/ask", json={"question": Q02})

    assert response.status_code == 200

    actual = AnswerResponse.model_validate(response.json())

    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None

    # 요구한 세 값을 전부 확보했다. 일부만 담긴 결과는 답이 아니다.
    assert len(actual.facts) == 3

    by_metric = {fact.metric: fact for fact in actual.facts}

    assert set(by_metric) == {
        MetricConcept.CONTRACT_AMOUNT,
        MetricConcept.CONTRACT_START_DATE,
        MetricConcept.CONTRACT_END_DATE,
    }

    amount = by_metric[MetricConcept.CONTRACT_AMOUNT]

    assert amount.normalized_value == EXPECTED_AMOUNT
    assert amount.normalized_unit == EXPECTED_AMOUNT_UNIT

    assert by_metric[MetricConcept.CONTRACT_START_DATE].normalized_value == (
        EXPECTED_START_DATE
    )
    assert by_metric[MetricConcept.CONTRACT_END_DATE].normalized_value == EXPECTED_END_DATE

    # 확정한 공시가 질문이 가리킨 그 계약인가. 같은 달의 다른 계약을 답하지 않았다.
    assert {item.doc_id for item in actual.evidence} == {EXPECTED_DOC_ID}

    # 각 Fact의 근거가 응답 안에서 실제로 풀린다. 개수만 세면 연결이 끊긴 채로도 통과한다.
    assert len(actual.evidence) == 3

    response_evidence_ids = {item.evidence_id for item in actual.evidence}

    for fact in actual.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= response_evidence_ids

    # 문장이 확보한 값을 그대로 말하는가. 원문 표기로 적힌다.
    assert "1,933,600,000,000원" in actual.answer
    assert f"{EXPECTED_START_DATE}부터 {EXPECTED_END_DATE}까지" in actual.answer


@pytest.mark.corpus
def test_latin_counterparty_spelling_fails_closed() -> None:
    """
    원문이 한글 상호로 적은 상대방을 라틴 표기로 물으면 찾지 못한다.

    이 회귀는 현재 동작을 고정하는 것이지 바람직한 동작을 고정하는 것이 아니다.
    표기를 잇는 규칙(음차·별칭 대응표)은 아직 정해지지 않았고, 정해지기 전까지는 비슷한
    이름으로 대신 고르지 않는 쪽이 맞다. 부분 일치나 음차 추정을 넣으면 "북미 지역 선주"
    같은 다른 계약이 후보로 끌려온다.

    규칙이 정해지면 이 테스트가 먼저 깨져서 알려 준다.
    """
    _require_actual_corpus()

    response = client.post("/api/ask", json={"question": Q02_LATIN})

    assert response.status_code == 200

    actual = AnswerResponse.model_validate(response.json())

    # 다른 상대방의 계약을 대신 답하지 않는다.
    assert actual.status is AnswerabilityStatus.UNANSWERABLE
    assert actual.facts == []
    assert actual.evidence == []


@pytest.mark.corpus
@pytest.mark.parametrize(
    "question",
    [
        f"한화오션이 2025년 9월 {COUNTERPARTY}과 체결한 계약의 계약금액은?",
        f"한화오션이 2025년 9월 {COUNTERPARTY}과 체결한 계약의 계약기간은?",
    ],
    ids=["amount_only", "period_only"],
)
def test_unsupported_contract_field_shapes_do_not_reach_the_answer_builder(
    question: str,
) -> None:
    """
    지원하지 않는 필드 조합이 500으로 새어 나가지 않는다.

    예전에는 계약금액만, 또는 계약기간만 물은 질문도 계획이 되고 handler를 통과했다.
    문장을 만드는 자리에서야 형태가 정해지지 않았다는 것이 드러났고, 그때는 결과가 이미
    ANSWERABLE로 판정된 뒤라 예외가 그대로 올라가 500이 됐다. 사용자에게는 서버가 고장난
    것으로 보이지만 실제로는 지원 범위 밖의 질문이었다.

    지금은 query_analyzer가 계획 단계에서 막는다. 그래서 BLOCKED가 되고, 이 API의 기존
    계약대로 422 오류 봉투로 나간다(api.service.AgentExecutionHTTPError.status_code).
    500과 422를 가르는 것이 이 회귀의 핵심이다. 둘 다 오류 응답이지만 500은 서버가 고장난
    것이고 422는 질문이 지원 범위 밖이라는 뜻이며, 받는 쪽의 다음 행동이 다르다.

    200을 기대하지 않는 이유:
        BLOCKED는 답이 아니다. 200으로 나가려면 실행이 COMPLETED로 끝나야 하며, 그때만
        AnswerResponse가 만들어진다. 막힌 결과를 200에 담으면 답이 없는 응답과 답이 있는
        응답이 같은 봉투로 나가 구분되지 않는다.

    stage와 reason까지 보는 이유:
        422라는 것만으로는 어디서 막혔는지 알 수 없다. FastAPI의 요청 검증 실패도 422이며
        (test_api_ask.py의 test_request_validation_422_remains_distinct_from_agent_blocked_422),
        그쪽은 detail 배열을 담아 봉투 자체가 다르다. query_plan 단계의
        unsupported_contract_fields까지 고정해야 이 회귀가 실제로 이 경로를 지킨다.

    코퍼스를 요구하는 이유는 이 경로가 manifest에서 기업 목록을 읽은 뒤에 열리기 때문이다.
    막히는 자리는 그보다 뒤인 질의 해석 단계다.
    """
    _require_actual_corpus()

    response = client.post("/api/ask", json={"question": question})

    # 지원하지 않는 질문은 이 API의 기존 BLOCKED 오류 계약으로 나간다. 서버 오류(500)가 아니다.
    assert response.status_code == 422

    payload = response.json()

    assert payload["execution_status"] == "BLOCKED"
    assert payload["error"]["stage"] == "query_plan"
    assert payload["error"]["reason"] == "unsupported_contract_fields"
