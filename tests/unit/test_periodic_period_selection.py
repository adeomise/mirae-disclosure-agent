"""Synthetic regressions for explicit YTD selection; no corpus values stored."""

import json

import pytest

from disclosure_agent.periodic.adapter import adapt_financial_fact
from disclosure_agent.periodic.extractor import ExtractionBlockedError, extract_financial_fact
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    filter_filings,
    list_filings,
    locate_filing,
)


@pytest.mark.parametrize("month,expected", [(3, [3]), (9, [9]), (6, []), (None, [3, 9])])
def test_quarter_locator_month(tmp_path, month, expected):
    records = []
    for m in (3, 9):
        directory = tmp_path / f"sample_{m}"
        directory.mkdir()
        (directory / "sample.xml").write_text("<DOCUMENT/>")
        records.append(
            dict(
                doc_id=f"periodic_SAMPLE_{m}",
                corp_name="테스트기업",
                doc_group="periodic",
                doc_subtype="quarter",
                base_year=2025,
                base_month=m,
                rcept_no=f"SAMPLE_{m}",
                file_path=directory.name,
                is_correction=False,
            )
        )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("\n".join(json.dumps(r) for r in records))
    filters = dict(
        company="테스트기업",
        document_class="periodic",
        document_subtype="quarter",
        year=2025,
        base_month=month,
    )
    assert [r["base_month"] for r in filter_filings(records, **filters)] == expected
    lookup = dict(manifest_path=manifest, corpus_root=tmp_path, **filters)
    assert [f.base_month for f in list_filings(**lookup)] == expected
    if expected:
        assert locate_filing(**lookup).base_month == expected[-1]
    else:
        with pytest.raises(FilingNotFoundError, match="조건에 맞는 공시"):
            locate_filing(**lookup)


def period_document(month, *, context=True, scope="consolidated"):
    code = {3: "dFQA", 6: "dHYA", 9: "dTQA", 12: "dFY"}[month]
    member = "ConsolidatedMember" if scope == "consolidated" else "SeparateMember"

    def value(text, period, year=2025):
        cell = {"type": "TE", "text": text, "acode": "ifrs-full_Revenue"}
        if context:
            cell["acontext"] = (
                f"CFY{year}{period}_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_"
                f"ifrs-full_{member}"
            )
        return cell

    table = dict(
        table_id="table_SAMPLE",
        section_id="section_SAMPLE",
        table_group_class="primary_statement",
        section_path=["연결재무제표" if scope == "consolidated" else "별도재무제표"],
        unit="(단위 : 백만원)",
        own_unit="(단위 : 백만원)",
        attributes={"aclass": "{XBRL}IS_C1" if scope == "consolidated" else "{XBRL}IS_S1"},
        rows=[
            [
                {"type": "TH", "text": "항목", "rowspan": "2"},
                {"type": "TH", "text": "2025년", "colspan": "2"},
                {"type": "TH", "text": "2024년"},
            ],
            [
                {"type": "TH", "text": "3개월"},
                {"type": "TH", "text": "누적"},
                {"type": "TH", "text": "누적"},
            ],
            [
                {"type": "TD", "text": "매출액"},
                value("30", code[:-1] + "Q"),
                value("100", code),
                value("80", code, 2024),
            ],
        ],
    )
    return {
        "metadata": {
            "doc_id": "periodic_SAMPLE",
            "corp_name": "테스트기업",
            "report_name": "사업보고서" if month == 12 else "분기보고서",
        },
        "tables": [table],
        "sections": [{"section_id": "section_SAMPLE", "title": table["section_path"][0]}],
    }


@pytest.mark.parametrize(
    "month,subtype", [(3, "quarter"), (6, "half"), (9, "quarter"), (12, "annual")]
)
@pytest.mark.parametrize("scope", ["consolidated", "separate"])
def test_explicit_period_selects_ytd_and_preserves_evidence(month, subtype, scope):
    doc = period_document(month, scope=scope)
    fact = extract_financial_fact(
        doc,
        metric="revenue",
        fiscal_year=2025,
        accounting_scope=scope,
        document_subtype=subtype,
        base_month=month,
    )
    assert fact.raw_value == "100"
    assert fact.location.column_index == 2
    contract, evidence = adapt_financial_fact(fact, doc)
    assert evidence.doc_id == "periodic_SAMPLE"
    assert evidence.row_label == "매출액"
    if month != 12:
        assert evidence.column_label == "2025년 누적"
        assert contract.fiscal_period == f"{month}M YTD"


@pytest.mark.parametrize("month,subtype", [(3, "quarter"), (6, "half"), (9, "quarter")])
def test_header_only_period(month, subtype):
    doc = period_document(month, context=False)
    fact = extract_financial_fact(
        doc,
        metric="revenue",
        fiscal_year=2025,
        accounting_scope="consolidated",
        document_subtype=subtype,
        base_month=month,
    )
    assert fact.raw_value == "100"
    assert fact.acontext is None


@pytest.mark.parametrize("failure", ["duplicate", "missing_header", "conflict", "unknown_year"])
def test_interim_fail_closed(failure):
    doc = period_document(9, context=failure in ("duplicate", "conflict"))
    table = doc["tables"][0]
    if failure == "duplicate":
        import copy

        duplicate = copy.deepcopy(table)
        duplicate["table_id"] = "table_OTHER"
        doc["tables"].append(duplicate)
    elif failure in ("missing_header", "conflict"):
        table["rows"][1][1]["text"] = "3개월" if failure == "conflict" else ""
    else:
        table["rows"][0][1]["text"] = "제 99 기"
    with pytest.raises(ExtractionBlockedError):
        extract_financial_fact(
            doc,
            metric="revenue",
            fiscal_year=2025,
            accounting_scope="consolidated",
            document_subtype="quarter",
            base_month=9,
        )


def test_header_only_without_acode_reuses_exact_metric_label():
    doc = period_document(6, context=False)
    for cell in doc["tables"][0]["rows"][2]:
        cell.pop("acode", None)
    fact = extract_financial_fact(
        doc,
        metric="revenue",
        fiscal_year=2025,
        accounting_scope="consolidated",
        document_subtype="half",
        base_month=6,
    )
    assert fact.raw_value == "100"
    assert fact.acode is None and fact.acontext is None


def test_interim_requires_explicit_month_before_locator(monkeypatch):
    from disclosure_agent.agent import period_retrieval

    def forbidden(**kwargs):
        pytest.fail("must not select latest quarter without explicit month")

    monkeypatch.setattr(period_retrieval, "locate_filing", forbidden)
    result = period_retrieval.fetch_period_fact(
        company="테스트기업",
        fiscal_year=2025,
        metric="revenue",
        accounting_scope="consolidated",
        document_class="periodic",
        document_subtype="quarter",
        corpus_root="unused",
    )
    assert result.blocked and result.fact is None


def test_collect_forwards_month_and_keeps_contract_evidence(monkeypatch, tmp_path):
    from disclosure_agent.agent import period_retrieval
    from disclosure_agent.retrieval.metadata_filter import FilingRef

    def locate(**kwargs):
        assert kwargs["base_month"] == 9
        assert kwargs["document_subtype"] == "quarter"
        return FilingRef(
            doc_id="periodic_SAMPLE",
            corp_name="테스트기업",
            corp_code=None,
            stock_code=None,
            report_name="분기보고서",
            receipt_no="SAMPLE",
            receipt_date=None,
            document_class="periodic",
            doc_group="periodic",
            document_subtype="quarter",
            base_year=2025,
            base_month=9,
            is_correction=False,
            directory=tmp_path,
            main_document=tmp_path / "sample.xml",
        )

    monkeypatch.setattr(period_retrieval, "locate_filing", locate)
    monkeypatch.setattr(period_retrieval, "load_parsed_document", lambda path: period_document(9))
    result = period_retrieval.collect_period_facts(
        company="테스트기업",
        fiscal_years=[2025],
        metrics=["revenue"],
        accounting_scope="consolidated",
        document_class="periodic",
        document_subtype="quarter",
        base_month=9,
        corpus_root=tmp_path,
    )
    assert result.is_complete
    assert result.facts[0].fiscal_period == "9M YTD"
    assert result.evidence[0].column_label == "2025년 누적"


@pytest.mark.parametrize("month,subtype", [(3, "quarter"), (6, "half"), (9, "quarter")])
def test_bulk_period_inference_uses_report_month(month, subtype):
    from disclosure_agent.periodic.extractor import infer_period

    assert infer_period(
        "2025년 누적", {}, "분기보고서", document_subtype=subtype, base_month=month
    ) == (2025, f"{month}M YTD")
    assert infer_period(
        "2025년 3개월", {}, "분기보고서", document_subtype=subtype, base_month=month
    ) == (2025, "quarter")


@pytest.mark.parametrize("conflict", ["scope", "years", "unknown_context", "period", "prior"])
def test_conflicting_signals_fail_closed(conflict):
    doc = period_document(9)
    table = doc["tables"][0]
    if conflict == "scope":
        table["section_path"] = ["연결 별도 재무제표"]
    elif conflict == "years":
        table["rows"][0][1]["text"] = "2025년 2024년"
    elif conflict == "unknown_context":
        table["rows"][2][2]["acontext"] = "unrecognized"
    elif conflict == "prior":
        table["rows"][0][1]["text"] = "전기"
    else:
        table["rows"][0][1]["text"] = "2025년 반기"
    with pytest.raises(ExtractionBlockedError):
        extract_financial_fact(
            doc,
            metric="revenue",
            fiscal_year=2025,
            accounting_scope="consolidated",
            document_subtype="quarter",
            base_month=9,
        )
