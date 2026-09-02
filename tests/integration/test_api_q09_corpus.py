"""Q09 실제 코퍼스 HTTP 종단 검증.

대표 질문 하나를 HTTP 경계에서 실행해, 실제 사업보고서 두 건으로 어떤 공개 응답이 나가는지
고정한다. 합성 문서로는 확인할 수 없는 것이 둘 있다.

    1. 두 해의 사업의 개요 Section이 실제 서식에서 각각 하나로 확정되는가
    2. 그 둘을 항목 단위로 짝지을 수 없다는 판정이 실제 문서에서도 나오는가

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

COMPARED_FISCAL_YEARS = (2023, 2025)

DOC_SUBTYPE_ANNUAL = "annual"

# 원인 추론으로 읽히는 표현. Q09는 원인을 말하지 않는다.
CAUSAL_TOKENS = ("때문에", "영향으로", "따라서", "원인", "결과로")

# 변화가 없었다고 단정하는 표현. 짝짓지 못한 것을 변화 없음으로 바꾸지 않는다.
NO_CHANGE_ASSERTIONS = ("변화가 없습니다", "변화가 없었습니다", "달라지지 않았습니다", "동일합니다")

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


def _expected_doc_id(manifest_path: Path, fiscal_year: int) -> str:
    """해당 회계연도 사업보고서의 공개 식별자만 manifest에서 읽는다."""
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
                and not record.get("is_correction")
            ):
                records.append(record)

    if len(records) != 1:
        pytest.skip(
            f"{EXPECTED_COMPANY} {fiscal_year} 사업보고서가 1건이 아닙니다: {len(records)}건"
        )

    return str(records[0].get("doc_id") or f"periodic_{records[0]['rcept_no']}")


@pytest.mark.corpus
def test_q09_runs_through_actual_corpus_http_api() -> None:
    """
    Q09가 실제 코퍼스에서 비교 불가 판정으로 끝난다.

    UNANSWERABLE이지만 근거는 함께 나간다. 두 Section을 실제로 확보했다는 사실이 응답에
    남아야, 문서를 찾지 못한 경우(NOT_FOUND)와 구분된다.
    """
    manifest_path = _require_actual_corpus()

    assert get_ask_service not in app.dependency_overrides

    expected_doc_ids = {
        _expected_doc_id(manifest_path, fiscal_year)
        for fiscal_year in COMPARED_FISCAL_YEARS
    }

    response = client.post("/api/ask", json={"question": Q09})

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == AnswerabilityStatus.UNANSWERABLE.value
    assert payload["reason"] == AnswerabilityReason.NOT_COMPARABLE.value

    assert payload["answer"].strip()

    # 값을 만들지 않는 경로다. 공개 응답에 자리가 있는 것만 확인한다.
    assert payload["facts"] == []
    assert payload["event_histories"] == []

    evidence = payload["evidence"]

    assert len(evidence) >= 2

    # 양쪽 연도의 문서가 모두 근거에 남아 있어야 한다. 한쪽만 있으면 한 해만 보고 판정한
    # 것이고, 그 상태로는 "견줄 수 없다"고 말할 수 없다.
    assert {item["doc_id"] for item in evidence} == expected_doc_ids

    for item in evidence:
        assert item["section_path"][-1].endswith("사업의 개요")


@pytest.mark.corpus
def test_q09_answer_names_both_years_without_inferring_causes() -> None:
    """
    문장이 두 회계연도를 밝히고, 원인도 '변화 없음'도 말하지 않는다.

    사업연도는 답변 본문이 아니라 근거로 밝힌다. 근거의 doc_id가 어느 해의 보고서인지를
    고정하며, 본문은 어느 범위를 견주려 했는지를 말한다.
    """
    manifest_path = _require_actual_corpus()

    expected_doc_ids = {
        _expected_doc_id(manifest_path, fiscal_year)
        for fiscal_year in COMPARED_FISCAL_YEARS
    }

    payload = client.post("/api/ask", json={"question": Q09}).json()

    answer = payload["answer"]

    for token in CAUSAL_TOKENS:
        assert token not in answer, f"원인 추론 표현이 답변에 있습니다: {token}"

    for token in NO_CHANGE_ASSERTIONS:
        assert token not in answer, f"변화 없음을 단정하는 표현이 답변에 있습니다: {token}"

    # 두 사업연도를 견주려 했다는 사실이 응답에서 드러나야 한다.
    assert "두 사업연도" in answer
    assert {item["doc_id"] for item in payload["evidence"]} == expected_doc_ids
