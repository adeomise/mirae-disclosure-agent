"""Actual-corpus HTTP regression for the representative Pattern B question (Q05).

이 테스트가 감시하는 것:
    Q05("한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?")가 HCX 없이도 /api/ask에서 200으로
    끝나는가. 질문에 연도가 없으므로 코퍼스 기준 최신 사업보고서 회계연도로 옮겨야 하고,
    사업의 개요와 주요 제품 두 범위가 한 문서에서 함께 확정돼야 한다.

    실제 공시 수치와 접수번호는 적지 않는다(AGENTS.md). 대상 문서의 식별자는 manifest에서
    읽어 견주며, 상태·범위 경로처럼 값을 적지 않고도 확인할 수 있는 것만 고정한다.

    근거 건수는 못박지 않는다. 그 값은 코퍼스의 해당 문서 구조에 종속되며, 여기서 고정하는
    것은 "두 범위 모두 근거를 갖고, 근거 없이 답하지 않는다"이다.

    source_documents도 같은 방식으로 본다. 값은 manifest 레코드에서 읽어 견주고, 건수는
    "근거가 가리키는 문서 집합과 정확히 같다"로 고정한다 — 근거가 몇 건이든 문서는 그 집합
    크기만큼이어야 하며, 화면이 Evidence.doc_id로 join하는 상대가 곧 이 목록이다.
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

Q05 = "한미반도체는 무슨 사업을 하고 주력 제품이 뭐야?"

EXPECTED_COMPANY = "한미반도체"

# 표준 서식에서 두 범위가 놓이는 자리.
EXPECTED_SECTION_ANCESTOR = "사업의 내용"
EXPECTED_OVERVIEW_TITLE = "사업의 개요"
EXPECTED_PRODUCTS_TITLE = "주요 제품"

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


def _expected_q05_record(manifest_path: Path) -> dict:
    """
    Q05가 골라야 하는 문서의 manifest 레코드를 읽는다.

    기준 연도는 그 기업의 사업보고서가 있는 가장 최신 회계연도이고(정정본 포함), 그 해의
    후보는 정정본을 포함해 정확히 1건이어야 한다. 둘 이상이면 runtime이 ambiguous_filing으로
    멈추므로 이 테스트의 전제가 성립하지 않는다.

    레코드를 통째로 돌려주는 이유:
        doc_id뿐 아니라 source_documents가 실어야 할 값(보고서 정식 명칭, 회계기간, 접수번호,
        공시일)도 같은 레코드에서 나온다. 그 값들을 여기 적으면 실제 공시 값을 코드에 남기게
        되므로(AGENTS.md), manifest에서 읽어 견준다.
    """
    annual_records = []

    with manifest_path.open(encoding="utf-8") as manifest:
        for line in manifest:
            if line.strip():
                record = json.loads(line)

                if (
                    normalize_company_name(str(record.get("corp_name", "")))
                    == normalize_company_name(EXPECTED_COMPANY)
                    and record.get("doc_group") == "periodic"
                    and record.get("doc_subtype") == DOC_SUBTYPE_ANNUAL
                    and isinstance(record.get("base_year"), int)
                ):
                    annual_records.append(record)

    assert annual_records

    latest_year = max(record["base_year"] for record in annual_records)
    candidates = [record for record in annual_records if record["base_year"] == latest_year]

    assert len(candidates) == 1, "Q05 corpus premise: exactly one latest annual filing"
    record = candidates[0]

    assert "사업보고서" in str(record.get("report_nm", ""))

    return record


def _expected_q05_doc_id(record: dict) -> str:
    return str(record.get("doc_id") or f"periodic_{record['rcept_no']}")


@pytest.mark.corpus
def test_q05_runs_through_actual_corpus_http_api() -> None:
    manifest_path = _require_actual_corpus()
    assert get_ask_service not in app.dependency_overrides

    response = client.post("/api/ask", json={"question": Q05})

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

    # 요약 구조는 실행 결과에만 있고 공개 Contract는 싣지 않는다.
    assert "calculations" not in payload
    assert "comparisons" not in payload
    assert "document_summaries" not in payload

    assert actual.evidence

    evidence_ids = [evidence.evidence_id for evidence in actual.evidence]
    assert all(evidence_ids)
    assert len(evidence_ids) == len(set(evidence_ids))

    expected_record = _expected_q05_record(manifest_path)
    expected_doc_id = _expected_q05_doc_id(expected_record)
    assert {evidence.doc_id for evidence in actual.evidence} == {expected_doc_id}

    # 근거가 가리키는 문서 집합과 source_documents의 문서 집합이 정확히 같다. 두 범위의
    # 근거가 모두 같은 문서에서 나오므로 문서는 근거 건수와 무관하게 1건으로 접힌다.
    assert {document.doc_id for document in actual.source_documents} == {
        evidence.doc_id for evidence in actual.evidence
    }
    assert len(actual.source_documents) == 1

    # 값은 manifest 레코드에서 그대로 옮겨 온 것이어야 한다. 질문 문자열이나 Evidence.doc_id
    # 에서 지어낸 값이면 여기서 어긋난다.
    source_document = actual.source_documents[0]

    assert source_document.company == EXPECTED_COMPANY
    assert source_document.report_name == expected_record["report_nm"]
    assert source_document.fiscal_year == expected_record["base_year"]
    assert source_document.document_subtype == DOC_SUBTYPE_ANNUAL
    assert source_document.base_month == expected_record["base_month"]
    assert source_document.receipt_no == expected_record["rcept_no"]
    assert source_document.filing_date is not None
    assert source_document.filing_date.strftime("%Y%m%d") == expected_record["rcept_dt"]

    # 두 범위가 서로 다른 Section 경로 하나씩으로 확정되고, 두 경로 모두 근거를 갖는다.
    section_paths = {tuple(evidence.section_path) for evidence in actual.evidence}
    assert len(section_paths) == 2

    for section_path in section_paths:
        assert section_path
        assert any(EXPECTED_SECTION_ANCESTOR in part for part in section_path[:-1])

    titles = {section_path[-1] for section_path in section_paths}
    assert any(EXPECTED_OVERVIEW_TITLE in title for title in titles)
    assert any(EXPECTED_PRODUCTS_TITLE in title for title in titles)

    # Evidence may contain only text, only tables, or both. This is an allowlist for every
    # returned location type, not a requirement that both types be present.
    assert {evidence.location_type for evidence in actual.evidence} <= {
        EvidenceLocationType.TEXT,
        EvidenceLocationType.TABLE,
    }
    assert all(evidence.raw_text and evidence.raw_text.strip() for evidence in actual.evidence)

    # 답은 결정론 문장이다. 두 범위의 위치를 말하고, 원문에서 새 서술을 만들지 않는다.
    assert actual.answer.strip()
    assert EXPECTED_OVERVIEW_TITLE in actual.answer
    assert EXPECTED_PRODUCTS_TITLE in actual.answer
