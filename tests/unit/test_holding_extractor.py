from __future__ import annotations

from pathlib import Path

import pytest

from disclosure_agent.common.enums import FactAccountingScope, MetricConcept
from disclosure_agent.holding.extractor import (
    DuplicateHoldingTargetError,
    InvalidHoldingTargetValueError,
    extract_holding_facts,
)
from disclosure_agent.holding.parser import parse_holding_xml

PII_VALUES = (
    "홍길동",
    "1990-01-02",
    "서울특별시 테스트로 1",
    "010-1234-5678",
)


def _document(tmp_path: Path, cells: str, *, extra_rows: str = "") -> dict:
    path = tmp_path / "holding.xml"
    path.write_text(
        f"""<DOCUMENT><BODY><SECTION-1><TABLE><TBODY>
        <TR><TD>{PII_VALUES[0]}</TD><TD>{PII_VALUES[1]}</TD>{cells}
            <TD>{PII_VALUES[2]}</TD><TD>{PII_VALUES[3]}</TD></TR>
        {extra_rows}
        </TBODY></TABLE></SECTION-1></BODY></DOCUMENT>""",
        encoding="utf-8",
    )
    return parse_holding_xml(path, doc_id="holding_SYNTHETIC")


def test_extracts_exact_current_holding_targets_as_contracts(tmp_path: Path) -> None:
    document = _document(
        tmp_path,
        '<TE ACODE="THS_STK_CNT">1,237,964,708</TE>'
        '<TE ACODE="THS_STK_RT">20.74</TE>',
    )

    result = extract_holding_facts(document, target_company="테스트발행회사")

    assert len(result.facts) == len(result.evidence) == 2
    assert result.missing_metrics == ()
    facts = {fact.metric: fact for fact in result.facts}
    assert facts[MetricConcept.HOLDING_SHARES].normalized_value == "1237964708"
    assert facts[MetricConcept.HOLDING_SHARES].normalized_unit == "shares"
    assert facts[MetricConcept.HOLDING_RATIO].normalized_value == "20.74"
    assert facts[MetricConcept.HOLDING_RATIO].normalized_unit == "percent"
    assert all(fact.company == "테스트발행회사" for fact in result.facts)
    assert all(fact.fiscal_year is None and fact.fiscal_period is None for fact in result.facts)
    assert all(
        fact.accounting_scope is FactAccountingScope.NOT_APPLICABLE
        for fact in result.facts
    )
    evidence_by_id = {item.evidence_id: item for item in result.evidence}
    assert all(fact.evidence_ids[0] in evidence_by_id for fact in result.facts)
    assert {item.raw_text for item in result.evidence} == {"1,237,964,708", "20.74"}
    assert {item.provenance.acode for item in result.evidence} == {
        "THS_STK_CNT",
        "THS_STK_RT",
    }
    assert all(item.provenance.table_id == "table_0001" for item in result.evidence)
    assert all(item.provenance.row_index == 0 for item in result.evidence)
    assert {item.provenance.column_index for item in result.evidence} == {2, 3}
    assert all(
        item.row_label is None
        and item.column_label is None
        and item.table_name is None
        and item.field_name is None
        for item in result.evidence
    )


def test_ignores_unrelated_acodes_and_forbids_label_fallback(tmp_path: Path) -> None:
    document = _document(
        tmp_path,
        '<TE ACODE="BFR_STK_CNT">111</TE>'
        '<TE ACODE="MDF_STK_RT">1.1</TE>'
        '<TE ACODE="HLD_STK_CNT">222</TE>'
        '<TE ACODE="STK_CNT">333</TE>'
        '<TD>이번 보고 보유주식 수</TD><TE ACODE="SIMILAR_CODE">999</TE>',
    )

    result = extract_holding_facts(document, target_company="테스트발행회사")

    assert result.facts == ()
    assert result.evidence == ()
    assert result.missing_metrics == (
        MetricConcept.HOLDING_SHARES,
        MetricConcept.HOLDING_RATIO,
    )


def test_missing_target_preserves_the_available_fact_and_evidence(tmp_path: Path) -> None:
    document = _document(tmp_path, '<TE ACODE="THS_STK_CNT">1,000</TE>')

    result = extract_holding_facts(document, target_company="테스트발행회사")

    assert [fact.metric for fact in result.facts] == [MetricConcept.HOLDING_SHARES]
    assert len(result.evidence) == 1
    assert result.missing_metrics == (MetricConcept.HOLDING_RATIO,)


@pytest.mark.parametrize("acode", ["THS_STK_CNT", "THS_STK_RT"])
def test_duplicate_target_fails_closed_without_source_text_leakage(
    tmp_path: Path,
    acode: str,
) -> None:
    first_value = "987654321" if acode == "THS_STK_CNT" else "98.76"
    second_value = "123456789" if acode == "THS_STK_CNT" else "12.34"
    document = _document(
        tmp_path,
        f'<TE ACODE="{acode}">{first_value}</TE>',
        extra_rows=(
            f'<TR><TD>{PII_VALUES[0]}</TD><TE ACODE="{acode}">{second_value}</TE></TR>'
        ),
    )

    with pytest.raises(DuplicateHoldingTargetError) as caught:
        extract_holding_facts(document, target_company="테스트발행회사")

    message = str(caught.value)
    assert acode in message
    assert "count=2" in message
    assert first_value not in message
    assert second_value not in message
    assert all(value not in message for value in PII_VALUES)


@pytest.mark.parametrize(
    ("acode", "raw_value"),
    [
        ("THS_STK_CNT", "홍길동"),
        ("THS_STK_RT", "010-1234-5678"),
    ],
)
def test_invalid_target_fails_closed_without_source_text_leakage(
    tmp_path: Path,
    acode: str,
    raw_value: str,
) -> None:
    document = _document(
        tmp_path,
        f'<TE ACODE="{acode}">{raw_value}</TE>',
    )

    with pytest.raises(InvalidHoldingTargetValueError) as caught:
        extract_holding_facts(document, target_company="테스트발행회사")

    message = str(caught.value)
    assert acode in message
    assert raw_value not in message
    assert all(value not in message for value in PII_VALUES)


def test_extraction_output_does_not_leak_pii_from_target_row(tmp_path: Path) -> None:
    document = _document(
        tmp_path,
        '<TE ACODE="THS_STK_CNT">1,000</TE>'
        '<TE ACODE="THS_STK_RT">2.5</TE>',
    )

    result = extract_holding_facts(document, target_company="테스트발행회사")
    serialized = repr(result) + " ".join(
        str(item.model_dump()) for item in (*result.facts, *result.evidence)
    )

    assert all(value not in serialized for value in PII_VALUES)
    assert {item.raw_text for item in result.evidence} == {"1,000", "2.5"}
