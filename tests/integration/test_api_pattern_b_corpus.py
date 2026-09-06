"""Actual-corpus HTTP regression for the representative Pattern B question (Q04).

이 테스트가 감시하는 것:
    Q04가 HCX 없이도 /api/ask에서 200으로 끝나는가. Pattern B의 결과에는 Fact가 없으므로,
    Fact가 있어야만 문장을 만들 수 있던 이전 Answer Builder였다면 이 경로가 500이 된다.

    실제 공시 수치와 접수번호는 적지 않는다(AGENTS.md). 대신 상태·건수·범위 경로처럼 값을
    적지 않고도 확인할 수 있는 것만 고정한다.
"""

import json
import os
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from disclosure_agent.api.dependencies import get_ask_service
from disclosure_agent.api.main import app
from disclosure_agent.common.enums import AnswerabilityStatus, EvidenceLocationType
from disclosure_agent.common.schemas import AnswerResponse
from disclosure_agent.retrieval.metadata_filter import (
    DOC_SUBTYPE_ANNUAL,
    normalize_company_name,
)

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

Q04 = "삼성바이오로직스 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘."

# 표준 서식에서 설비 투자 범위가 놓이는 자리.
EXPECTED_SECTION_ANCESTOR = "사업의 내용"
EXPECTED_SECTION_TITLE = "생산설비"
EXPECTED_COMPANY = "삼성바이오로직스"
EXPECTED_FISCAL_YEAR = 2025

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


def _expected_q04_record(manifest_path: Path) -> dict:
    """Read Q04's selected manifest record.

    doc_id뿐 아니라 source_documents가 실어야 할 값(보고서 정식 명칭, 회계기간, 접수번호,
    공시일)도 같은 레코드에서 나온다. 그 값을 코드에 적지 않고(AGENTS.md) manifest에서 읽어
    견주기 위해 레코드를 통째로 돌려준다.
    """
    records = []

    with manifest_path.open(encoding="utf-8") as manifest:
        for line in manifest:
            if line.strip():
                record = json.loads(line)

                if (
                    normalize_company_name(str(record.get("corp_name", "")))
                    == normalize_company_name(EXPECTED_COMPANY)
                    and record.get("doc_group") == "periodic"
                    and record.get("doc_subtype") == DOC_SUBTYPE_ANNUAL
                    and record.get("base_year") == EXPECTED_FISCAL_YEAR
                    and not record.get("is_correction")
                ):
                    records.append(record)

    assert len(records) == 1
    record = records[0]

    assert "사업보고서" in str(record.get("report_nm", ""))

    return record


def _expected_q04_doc_id(record: dict) -> str:
    return str(record.get("doc_id") or f"periodic_{record['rcept_no']}")


@pytest.mark.corpus
def test_q04_runs_through_actual_corpus_http_api() -> None:
    manifest_path = _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q04})

    assert response.status_code == 200

    payload = response.json()
    actual = AnswerResponse.model_validate(payload)

    assert actual.status is AnswerabilityStatus.ANSWERABLE
    assert actual.reason is None

    # Pattern B는 값을 뽑지 않는다. 최소 Fact 요건이 0이라는 것이 확정 사항이다
    # (docs/agent_design/evidence_and_answerability.md 유형별 최소 요건).
    assert actual.facts == []
    assert actual.events == []
    assert actual.event_links == []
    assert actual.event_histories == []

    # AgentExecutionResult에는 이 구조가 있지만 현재 AnswerResponse가 공개하지 않는다. 문장으로
    # 역추론하지 않고, 공개 Contract가 계산/비교/요약 구조를 떨어뜨린다는 사실을 명시한다.
    assert "calculations" not in payload
    assert "comparisons" not in payload
    assert "document_summaries" not in payload

    # 근거는 항목 수만큼 실린다. 개수를 못박지 않는 이유는 그 값이 코퍼스의 해당 문서 구조에
    # 종속되기 때문이며, 여기서 고정하는 것은 "근거 없이 답하지 않는다"이다.
    assert actual.evidence

    evidence_ids = [evidence.evidence_id for evidence in actual.evidence]
    assert all(evidence_ids)
    assert len(evidence_ids) == len(set(evidence_ids))

    expected_record = _expected_q04_record(manifest_path)
    expected_doc_id = _expected_q04_doc_id(expected_record)
    assert {evidence.doc_id for evidence in actual.evidence} == {expected_doc_id}

    # 근거가 가리키는 문서 집합과 source_documents의 문서 집합이 정확히 같다.
    assert {document.doc_id for document in actual.source_documents} == {
        evidence.doc_id for evidence in actual.evidence
    }
    assert len(actual.source_documents) == 1

    # 값은 실제로 선택된 공시의 manifest 레코드에서 그대로 옮겨 온 것이어야 한다.
    source_document = actual.source_documents[0]

    assert source_document.company == EXPECTED_COMPANY
    assert source_document.report_name == expected_record["report_nm"]
    assert source_document.fiscal_year == EXPECTED_FISCAL_YEAR
    assert source_document.document_subtype == DOC_SUBTYPE_ANNUAL
    assert source_document.base_month == expected_record["base_month"]
    assert source_document.receipt_no == expected_record["rcept_no"]
    assert source_document.filing_date is not None
    assert source_document.filing_date.strftime("%Y%m%d") == expected_record["rcept_dt"]

    section_paths = {tuple(evidence.section_path) for evidence in actual.evidence}
    assert len(section_paths) == 1

    section_path = next(iter(section_paths))
    assert section_path
    assert any(EXPECTED_SECTION_ANCESTOR in part for part in section_path[:-1])
    assert EXPECTED_SECTION_TITLE in section_path[-1]

    # Evidence may contain only text, only tables, or both. This is an allowlist for every
    # returned location type, not a requirement that both types be present.
    assert {evidence.location_type for evidence in actual.evidence} <= {
        EvidenceLocationType.TEXT,
        EvidenceLocationType.TABLE,
    }
    assert all(evidence.raw_text and evidence.raw_text.strip() for evidence in actual.evidence)

    assert actual.answer.strip()
