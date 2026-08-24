import pytest

from disclosure_agent.common.enums import FactAccountingScope, MetricConcept
from disclosure_agent.periodic.extractor import extract_financial_facts_with_evidence


def cell(
    text: str,
    *,
    table_id: str,
    row: int,
    col: int,
    cell_type: str = "TD",
    **attributes: str,
) -> dict:
    return {
        "type": cell_type,
        "text": text,
        "provenance": {
            "section_id": "section_0001",
            "table_group_id": "table_group_0001",
            "table_id": table_id,
            "row_index": row,
            "col_index": col,
        },
        **attributes,
    }


def financial_table(
    *,
    table_id: str = "table_0001",
    label: str = "매출액",
    values: tuple[str, ...] = ("100", "90"),
    unit: str = "(단위 : 백만원)",
) -> dict:
    header = [cell("", table_id=table_id, row=0, col=0, cell_type="TH")]
    for index, year in enumerate(("2025", "2024")[: len(values)], start=1):
        header.append(
            cell(f"제 {26 - index} 기 {year}", table_id=table_id, row=0, col=index, cell_type="TH")
        )
    value_row = [cell(label, table_id=table_id, row=1, col=0)]
    value_row.extend(
        cell(value, table_id=table_id, row=1, col=index)
        for index, value in enumerate(values, start=1)
    )
    return {
        "table_id": table_id,
        "title": None,
        "unit": unit,
        "own_unit": None,
        "section_id": "section_0001",
        "table_group_id": "table_group_0001",
        "section_path": ["III. 재무에 관한 사항", "2. 연결재무제표"],
        "raw_context": "연결재무제표",
        "attributes": {},
        "rows": [header, value_row],
    }


def parsed_document(
    table: dict,
    *,
    aclass: str = "{XBRL}IS_C1",
    context: str = "연결재무제표",
    unit: str = "(단위 : 백만원)",
) -> dict:
    group = {
        "table_group_id": "table_group_0001",
        "section_id": "section_0001",
        "title": context,
        "aclass": aclass,
        "raw_context": context,
        "section_path": table["section_path"],
        "unit": unit,
        "tables": [table],
    }
    return {
        "metadata": {
            "doc_id": "periodic_SAMPLE",
            "corp_name": "테스트기업",
            "report_name": "사업보고서",
        },
        "sections": [{"section_id": "section_0001"}],
        "table_groups": [group],
        # Parser의 hierarchy/flat compatibility view를 그대로 재현한다.
        "tables": [table],
    }


def test_revenue_facts_link_period_unit_scope_and_provenance() -> None:
    document = parsed_document(financial_table(values=("1,234", "900")))
    facts, evidence = extract_financial_facts_with_evidence(document)

    assert len(facts) == 2
    current = facts[0]
    assert current.metric is MetricConcept.REVENUE
    assert current.raw_value == "1,234"
    assert current.raw_unit == "백만원"
    assert current.normalized_value == "1234000000"
    assert current.normalized_unit == "KRW"
    assert current.fiscal_year == 2025
    assert current.fiscal_period == "annual"
    assert current.accounting_scope is FactAccountingScope.CONSOLIDATED
    assert current.evidence_ids == [evidence[0].evidence_id]
    assert evidence[0].field_name == (
        "table_id=table_0001;row_index=1;col_index=1;anegated=None"
    )
    assert evidence[0].raw_text == "1,234"

    assert evidence[0].provenance is not None

    provenance = evidence[0].provenance

    assert provenance.table_id == "table_0001"
    assert provenance.row_index == 1
    assert provenance.column_index == 1

    assert provenance.acode is None
    assert provenance.acontext is None

    assert provenance.unit_declaration == "(단위 : 백만원)"
    assert provenance.unit_source == "table_group"
    assert provenance.unit_multiplier == 1_000_000
    assert provenance.unit_trusted is True

    assert provenance.aclass == "{XBRL}IS_C1"
    assert provenance.table_classification == "primary_statement"


@pytest.mark.parametrize("label", ["매출원가", "매출채권", "매출총이익"])
def test_revenue_false_positives_are_rejected(label: str) -> None:
    facts, _ = extract_financial_facts_with_evidence(
        parsed_document(financial_table(label=label, values=("100",)))
    )
    assert facts == []


@pytest.mark.parametrize(
    ("aclass", "context", "expected"),
    [
        ("{XBRL}IS_C1", "연결재무제표", FactAccountingScope.CONSOLIDATED),
        ("{XBRL}IS_S1", "별도재무제표", FactAccountingScope.SEPARATE),
        ("NORMAL", "요약재무정보", FactAccountingScope.UNKNOWN),
    ],
)
def test_accounting_scope_uses_only_explicit_signals(
    aclass: str,
    context: str,
    expected: FactAccountingScope,
) -> None:
    table = financial_table(values=("100",))
    table["section_path"] = ["III. 재무에 관한 사항", context]
    table["raw_context"] = context
    facts, _ = extract_financial_facts_with_evidence(
        parsed_document(table, aclass=aclass, context=context)
    )
    assert facts[0].accounting_scope is expected


def test_group_units_do_not_leak_and_flat_view_is_deduplicated() -> None:
    first = financial_table(
        table_id="table_0001",
        values=("100",),
        # Parser가 잡은 행별 단위는 group의 재무표 단위를 덮어쓰면 안 된다.
        unit="기본주당이익 (단위 : 원)",
    )
    second = financial_table(table_id="table_0002", values=("200",), unit="(단위 : 원)")
    second["table_group_id"] = "table_group_0002"
    for row in second["rows"]:
        for item in row:
            item["provenance"]["table_group_id"] = "table_group_0002"
    document = parsed_document(first)
    document["table_groups"].append(
        {
            **document["table_groups"][0],
            "table_group_id": "table_group_0002",
            "unit": "(단위 : 원)",
            "tables": [second],
        }
    )
    document["tables"].append(second)

    facts, evidence = extract_financial_facts_with_evidence(document)

    assert len(facts) == 2
    assert [(fact.raw_unit, fact.normalized_value) for fact in facts] == [
        ("백만원", "100000000"),
        ("원", "200"),
    ]

    assert evidence[0].provenance is not None
    assert evidence[0].provenance.unit_source == "table_group"
    assert evidence[0].provenance.unit_declaration == "(단위 : 백만원)"
    assert evidence[0].provenance.unit_multiplier == 1_000_000
    assert evidence[0].provenance.unit_trusted is True


@pytest.mark.parametrize(
    "label",
    [
        "영업이익",
        "영업이익(손실)",
    ],
)
def test_operating_income_facts_are_emitted(label: str) -> None:
    document = parsed_document(
        financial_table(
            label=label,
            values=("20",),
        )
    )

    facts, evidence = extract_financial_facts_with_evidence(document)

    assert len(facts) == 1
    assert len(evidence) == 1

    fact = facts[0]

    assert fact.metric is MetricConcept.OPERATING_INCOME
    assert fact.raw_value == "20"
    assert fact.raw_unit == "백만원"
    assert fact.normalized_value == "20000000"
    assert fact.normalized_unit == "KRW"
    assert fact.fiscal_year == 2025
    assert fact.fiscal_period == "annual"
    assert (
        fact.accounting_scope
        is FactAccountingScope.CONSOLIDATED
    )
    assert fact.evidence_ids == [evidence[0].evidence_id]

    assert evidence[0].row_label == label
    assert evidence[0].raw_text == "20"



@pytest.mark.parametrize(
    "label",
    [
        "영업이익률",
        "영업이익증가율",
        "영업외이익",
    ],
)
def test_operating_income_false_positives_are_rejected(
    label: str,
) -> None:
    facts, _ = extract_financial_facts_with_evidence(
        parsed_document(
            financial_table(
                label=label,
                values=("20",),
            )
        )
    )

    assert facts == []