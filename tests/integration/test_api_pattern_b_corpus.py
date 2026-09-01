"""Actual-corpus HTTP regression for the representative Pattern B question (Q04).

이 테스트가 감시하는 것:
    Q04가 HCX 없이도 /api/ask에서 200으로 끝나는가. Pattern B의 결과에는 Fact가 없으므로,
    Fact가 있어야만 문장을 만들 수 있던 이전 Answer Builder였다면 이 경로가 500이 된다.

    실제 공시 수치와 접수번호는 적지 않는다(AGENTS.md). 대신 상태·건수·범위 경로처럼 값을
    적지 않고도 확인할 수 있는 것만 고정한다.
"""

import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityStatus, EvidenceLocationType
from disclosure_agent.common.schemas import AnswerResponse

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

Q04 = "삼성바이오로직스 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘."

# Q05·Q06은 요약 대상 문서를 고르는 정책이 정해지기 전까지 답하지 않는다.
#
#     Q05  기간 미지정 시의 기본 문서 선택 정책(open_decisions.md D5-a) 미정
#     Q06  거래소공시 단건 요약의 판본 선택 정책(open_decisions.md D5) 미정
#
# Q04를 열었다고 해서 이 둘이 조용히 임의의 문서를 고르기 시작하면 안 된다. 그 회귀는 200
# 응답으로 나타나므로 상태 코드로 고정한다.
Q05 = "한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?"
Q06 = "LG유플러스 파주 AIDC 투자 공시 내용 요약해줘."

# 표준 서식에서 설비 투자 범위가 놓이는 자리.
EXPECTED_SECTION_ANCESTOR = "사업의 내용"
EXPECTED_SECTION_TITLE = "생산설비"

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
def test_q04_runs_through_actual_corpus_http_api() -> None:
    _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q04})

    assert response.status_code == 200

    actual = AnswerResponse.model_validate(response.json())

    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None

    # Pattern B는 값을 뽑지 않는다. 최소 Fact 요건이 0이라는 것이 확정 사항이다
    # (docs/agent_design/evidence_and_answerability.md 유형별 최소 요건).
    assert actual.facts == []
    assert actual.events == []
    assert actual.event_histories == []

    # 근거는 항목 수만큼 실린다. 개수를 못박지 않는 이유는 그 값이 코퍼스의 해당 문서 구조에
    # 종속되기 때문이며, 여기서 고정하는 것은 "근거 없이 답하지 않는다"이다.
    assert actual.evidence

    assert actual.answer


@pytest.mark.corpus
def test_q04_evidence_stays_inside_the_requested_scope() -> None:
    """
    요약이 지정 범위를 벗어나지 않았는지 본다.

    Q04의 주요 실패 모드는 재무제표 주석의 투자 항목까지 끌어와 범위가 번지는 것이다
    (docs/agent_design/representative_questions.md Q04). 근거의 Section 경로를 모두 확인하면
    그 확산이 드러난다.
    """
    _require_actual_corpus()

    response = client.post("/api/ask", json={"question": Q04})

    assert response.status_code == 200

    actual = AnswerResponse.model_validate(response.json())

    doc_ids = {evidence.doc_id for evidence in actual.evidence}

    # 요약 대상은 문서 1건이다.
    assert len(doc_ids) == 1

    section_paths = {tuple(evidence.section_path) for evidence in actual.evidence}

    assert len(section_paths) == 1

    section_path = next(iter(section_paths))

    assert any(EXPECTED_SECTION_ANCESTOR in part for part in section_path[:-1])
    assert EXPECTED_SECTION_TITLE in section_path[-1]

    # 본문 근거와 표 근거를 한 종류로 뭉개지 않는다.
    assert {evidence.location_type for evidence in actual.evidence} <= {
        EvidenceLocationType.TEXT,
        EvidenceLocationType.TABLE,
    }

    for evidence in actual.evidence:
        assert evidence.raw_text


@pytest.mark.corpus
@pytest.mark.parametrize(
    "question",
    [
        pytest.param(Q05, id="q05-default-period-policy-pending"),
        pytest.param(Q06, id="q06-version-policy-pending"),
    ],
)
def test_pending_summary_policies_stay_closed(question: str) -> None:
    """
    Q04를 연 것이 Q05·Q06을 함께 열지 않는다.

    두 문항이 막히는 이유는 각각 D5-a와 D5이며, 어느 쪽도 이 PR에서 정하지 않았다. 정책 없이
    문서를 고르기 시작하면 200 응답에 근거까지 갖춘 답이 돌아오고, 그 선택이 곧 정책이 된다.
    """
    _require_actual_corpus()

    response = client.post("/api/ask", json={"question": question})

    assert response.status_code == 422

    payload = response.json()

    assert payload["execution_status"] == "BLOCKED"
    assert payload["error"]["stage"] == "query_plan"
