"""Compare selected facts against raw organizer XML, without storing source values."""

import os
from pathlib import Path

import pytest

from disclosure_agent.agent.period_retrieval import fetch_period_fact
from disclosure_agent.periodic.adapter import adapt_financial_fact
from disclosure_agent.periodic.extractor import extract_financial_fact
from disclosure_agent.periodic.parser import parse_xml_root
from disclosure_agent.retrieval.metadata_filter import load_manifest


@pytest.mark.corpus
@pytest.mark.parametrize(
    "company,month,subtype",
    [
        ("삼성전자", 3, "quarter"),
        ("삼성전자", 6, "half"),
        ("삼성전자", 9, "quarter"),
        ("삼성전자", 12, "annual"),
        ("세아베스틸지주", 6, "half"),
    ],
)
def test_actual_ytd_value_and_identity(company, month, subtype):
    root = os.environ.get("DISCLOSURE_CORPUS_ROOT")
    if not root:
        pytest.skip("DISCLOSURE_CORPUS_ROOT is not set")
    root = Path(root)
    manifest = Path(os.environ.get("DISCLOSURE_CORPUS_MANIFEST", root / "manifest.jsonl"))
    records = [
        r
        for r in load_manifest(manifest)
        if r.get("corp_name") == company
        and r.get("base_year") == 2025
        and r.get("base_month") == month
        and r.get("doc_subtype") == subtype
        and not r.get("is_correction")
    ]
    assert len(records) == 1
    found = fetch_period_fact(
        company=company,
        fiscal_year=2025,
        metric="revenue",
        accounting_scope="consolidated",
        document_class="periodic",
        document_subtype=subtype,
        base_month=month,
        corpus_root=root,
        manifest_path=manifest,
    )
    assert found.is_found, found.blocked
    assert found.filing.doc_id == records[0]["doc_id"]
    assert found.filing.base_month == month
    assert found.evidence.doc_id == records[0]["doc_id"]
    assert found.evidence.receipt_no == records[0]["rcept_no"]
    assert Path(found.evidence.source_path) == found.filing.main_document

    # Independent source oracle: current consolidated IS revenue row, explicit
    # cumulative second numeric cell for interim, first numeric cell for FY.
    xml = parse_xml_root(found.filing.main_document)
    groups = xml.xpath('//TABLE-GROUP[starts-with(@ACLASS, "{XBRL}IS_C")]')
    rows = [
        row
        for group in groups
        for row in group.xpath(".//TR")
        if row.xpath('./TE[@ACODE="ifrs-full_Revenue"]')
    ]
    assert len(rows) == 1
    row = rows[0]
    cells = row.xpath('./TE[@ACODE="ifrs-full_Revenue"]')
    selected = cells[0 if month == 12 else 1]
    expected = "".join(selected.itertext()).strip()
    assert found.fact.raw_value == expected
    assert found.evidence.quote == expected
    assert found.fact.acode == selected.get("ACODE")
    assert found.fact.acontext == selected.get("ACONTEXT")
    table = selected.getparent().getparent()
    while table.tag != "TABLE":
        table = table.getparent()
    raw_headers = ["".join(h.itertext()).strip() for h in table.xpath(".//TH")]
    contract, evidence = adapt_financial_fact(found.fact, found.document)
    assert evidence.doc_id == found.evidence.doc_id
    assert evidence.row_label == "".join(row[0].itertext()).strip()
    assert evidence.section_path
    if month != 12:
        assert "누적" in raw_headers and "3개월" in raw_headers
        assert "누적" in evidence.column_label
        assert contract.fiscal_period == f"{month}M YTD"
        assert evidence.provenance.table_id == found.fact.location.table_id
        assert evidence.provenance.acode == selected.get("ACODE")
        if month in (6, 9):
            assert expected != "".join(cells[0].itertext()).strip()
    else:
        baseline = extract_financial_fact(
            found.document, metric="revenue", fiscal_year=2025, accounting_scope="consolidated"
        )
        assert baseline == found.fact
        assert adapt_financial_fact(baseline, found.document) == (contract, evidence)
