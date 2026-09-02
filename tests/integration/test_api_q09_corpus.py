"""Q09 실제 코퍼스 HTTP 종단 검증.

대표 질문 하나를 HTTP 경계에서 실행해, 실제 코퍼스에서 어떤 공개 응답이 나가는지 고정한다.

Q09에는 문서 확정 결과에 따라 갈리는 두 가지 정당한 결말이 있다.

    1. 두 해 모두 판본이 하나로 확정됐지만 서술 항목을 짝지을 규칙이 없다
       -> COMPLETED / UNANSWERABLE / NOT_COMPARABLE
       (tests/integration/test_pattern_c_narrative.py가 합성 문서로 고정)
    2. 한 해의 판본 자체가 둘 이상이라 어느 것을 견줄지 정할 수 없다
       -> COMPLETED / UNRESOLVED / AMBIGUOUS_MATCH

실제 코퍼스는 2번이다. 2025 사업보고서에 원본과 정정본이 함께 있고, 정기공시의 판본 선택
정책(docs/agent_design/open_decisions.md D5)이 아직 없으므로 runtime은 원본을 조용히 고르는
대신 멈춘다. 이 파일은 그 관찰된 동작을 고정한다. 두 결말을 한 답으로 뭉개지 않는다.

합성 문서로는 확인할 수 없는 것이 여기 있다: 실제 manifest가 정말 그 모양인지, 그리고 그
모양에서 runtime이 정말 멈추는지.

공개 응답이 노출하는 필드만 확인한다. NarrativeComparisonResult는 AnswerResponse에 자리가
없으므로 여기서 보지 않으며, 구조화 결과의 모양은
tests/integration/test_pattern_c_narrative.py가 고정한다.

수치도 원문도 이 파일에 적지 않는다. 실제 공시 값을 저장소에 적지 않는다는 규칙 때문이며,
확인하는 것은 판정과 문서 식별자까지다.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityReason, AnswerabilityStatus
from disclosure_agent.retrieval.metadata_filter import normalize_company_name

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

# 대표 질문 원문. evaluation/questions.jsonl의 표기와 같아야 한다.
Q09 = "삼성바이오로직스 사업 구조가 2023년이랑 2025년이랑 어떻게 달라졌어?"

EXPECTED_COMPANY = "삼성바이오로직스"

DOC_SUBTYPE_ANNUAL = "annual"

# 원인 추론으로 읽히는 표현. Q09는 원인을 말하지 않는다.
CAUSAL_TOKENS = ("때문에", "영향으로", "따라서", "원인", "결과로")

# 변화가 없었다고 단정하는 표현. 견주지 못한 것을 변화 없음으로 바꾸지 않는다.
NO_CHANGE_ASSERTIONS = ("변화가 없습니다", "변화가 없었습니다", "달라지지 않았습니다", "동일합니다")

# 변화가 있었다고 단정하는 표현. 견주지 못했으므로 이쪽도 근거가 없다.
CHANGE_ASSERTIONS = ("달라졌습니다", "변경되었습니다", "바뀌었습니다", "재편")

client = TestClient(app)


def _require_actual_corpus() -> Path:
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

    return manifest_path


def _candidate_doc_ids(manifest_path: Path, fiscal_year: int) -> set[str]:
    """
    해당 회계연도 사업보고서 후보의 공개 식별자를 manifest에서 읽는다. 정정본을 포함한다.

    runtime과 같은 기준으로 후보를 센다. handler는 판본 선택 정책(D5)이 없어 정정본을
    후보에 넣고, 같은 해에 둘 이상이면 어느 것도 고르지 않는다. 여기서 정정본을 걸러 내면
    테스트가 runtime에 없는 원본 우선 정책을 스스로 정해 두는 것이 된다.

    후보가 여럿이어도 건너뛰지 않는다. 건너뛰면 실제 코퍼스에 판본이 둘 있다는 사실이
    결과에서 사라지고, 그 사실이 곧 Q09의 관찰된 동작을 바꾸는 조건이다.
    """
    records = []

    with manifest_path.open(encoding="utf-8") as manifest:
        for line in manifest:
            if not line.strip():
                continue

            record = json.loads(line)

            if (
                normalize_company_name(str(record.get("corp_name", "")))
                == normalize_company_name(EXPECTED_COMPANY)
                and record.get("doc_group") == "periodic"
                and record.get("doc_subtype") == DOC_SUBTYPE_ANNUAL
                and record.get("base_year") == fiscal_year
            ):
                records.append(record)

    if not records:
        pytest.skip(f"{EXPECTED_COMPANY} {fiscal_year} 사업보고서가 코퍼스에 없습니다")

    return {str(record.get("doc_id") or f"periodic_{record['rcept_no']}") for record in records}


@pytest.mark.corpus
def test_actual_corpus_has_the_version_shape_this_regression_depends_on() -> None:
    """
    실제 manifest가 이 회귀의 전제를 만족하는지 먼저 고정한다.

    FY2023은 정정본을 포함해도 1건, FY2025는 정정본을 포함하면 2건 이상이어야 한다. 이
    전제가 깨지면(예: 코퍼스가 갱신돼 정정본이 사라지면) 아래 API 검증의 기대값이 더는
    맞지 않으며, 그 사실은 API 결과가 아니라 여기서 먼저 드러나야 한다.
    """
    manifest_path = _require_actual_corpus()

    assert len(_candidate_doc_ids(manifest_path, 2023)) == 1
    assert len(_candidate_doc_ids(manifest_path, 2025)) > 1


@pytest.mark.corpus
def test_q09_runs_through_actual_corpus_http_api() -> None:
    """
    Q09가 실제 코퍼스에서 판본 모호 판정으로 끝난다.

    UNRESOLVED이며 UNANSWERABLE이 아니다. 문서가 없거나 견줄 수 없다고 판정한 것이 아니라,
    어느 판본을 견줄지 정하지 못한 것이다. 이 구분이 이 파일이 지키는 것이다.
    """
    manifest_path = _require_actual_corpus()

    assert get_ask_service not in app.dependency_overrides

    resolved_doc_ids = _candidate_doc_ids(manifest_path, 2023)
    ambiguous_doc_ids = _candidate_doc_ids(manifest_path, 2025)

    response = client.post("/api/ask", json={"question": Q09})

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == AnswerabilityStatus.UNRESOLVED.value
    assert payload["reason"] == AnswerabilityReason.AMBIGUOUS_MATCH.value

    assert payload["answer"].strip()

    # 값을 하나도 만들지 않는다. 공개 응답에 자리가 있는 것만 확인한다.
    assert payload["facts"] == []
    assert payload["events"] == []
    assert payload["event_histories"] == []

    # 남은 근거는 하나로 확정된 쪽(FY2023)의 것만이어야 한다. 모호한 해의 문서가 근거에
    # 있으면 판본을 조용히 고른 것이다. 양쪽 문서가 모두 있어야 한다고는 요구하지 않는다.
    # FY2025는 의도적으로 확정되지 않았다.
    retained = {item["doc_id"] for item in payload["evidence"]}

    assert retained <= resolved_doc_ids
    assert retained.isdisjoint(ambiguous_doc_ids)

    for item in payload["evidence"]:
        assert item["section_path"][-1].endswith("사업의 개요")


@pytest.mark.corpus
def test_q09_answer_does_not_assert_change_or_causes() -> None:
    """
    판본을 정하지 못한 답변은 사업 구조가 달라졌다고도, 달라지지 않았다고도 말하지 않는다.

    견주지 않았으므로 어느 쪽 진술도 근거가 없다. 원인도 적지 않는다.
    """
    _require_actual_corpus()

    answer = client.post("/api/ask", json={"question": Q09}).json()["answer"]

    for token in CAUSAL_TOKENS:
        assert token not in answer, f"원인 추론 표현이 답변에 있습니다: {token}"

    for token in NO_CHANGE_ASSERTIONS:
        assert token not in answer, f"변화 없음을 단정하는 표현이 답변에 있습니다: {token}"

    for token in CHANGE_ASSERTIONS:
        assert token not in answer, f"변화를 단정하는 표현이 답변에 있습니다: {token}"

    # 왜 답하지 못했는지가 드러나야 한다. 후보가 여럿이라는 사실이다.
    assert "여러 건" in answer
