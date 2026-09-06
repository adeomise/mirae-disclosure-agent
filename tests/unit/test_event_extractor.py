from pathlib import Path

import pytest

from disclosure_agent.event.adapter import adapt_extracted_event
from disclosure_agent.event.extractor import extract_event, match_field
from disclosure_agent.event.parser import parse_exchange_document


def parse_html(tmp_path: Path, body: str) -> dict:
    source = tmp_path / "SYNTHETIC.xml"
    source.write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    return parse_exchange_document(source, corp_name="테스트기업")


def test_supply_current_table_aliases_flow_through_parser_extractor_and_adapter(
    tmp_path: Path,
) -> None:
    document = parse_html(
        tmp_path,
        """
        <span>단일판매ㆍ공급계약 체결</span>
        <table id="XFormD1_Form0_Table0">
          <tr><td>2. 계약내역</td><td>최근매출액(원)</td><td>2,000</td></tr>
          <tr><td>계약금액 총액(원)</td><td>1,234</td></tr>
          <tr><td>3. 계약상대방</td><td>테스트 상대방</td></tr>
          <tr><td>6. 계약(수주)일</td><td>2099-01-02</td></tr>
        </table>
        """,
    )

    assert [
        (row["group"], row["raw_field"], row["row_index"])
        for row in document["rows"]
    ] == [
        ("계약내역", "최근매출액(원)", 0),
        ("계약내역", "계약금액 총액(원)", 1),
        ("계약상대방", "계약상대방", 2),
        ("계약(수주)일", "계약(수주)일", 3),
    ]

    extracted = extract_event(document)
    assert extracted["event_type"] == "supply_contract"
    assert extracted["fields"]["contract_amount"] == {
        "raw_group": "계약내역",
        "raw_field": "계약금액 총액(원)",
        "raw_value": "1,234",
        "raw_unit": "원",
        "normalized_value": 1234,
        "normalized_unit": "KRW",
        "provenance": {
            "table_id": "XFormD1_Form0_Table0",
            "row_index": 1,
            "field_cell_index": 0,
            "value_cell_index": 1,
        },
    }
    assert extracted["fields"]["counterparty"]["normalized_value"] == (
        "테스트 상대방"
    )
    assert extracted["fields"]["contract_date"]["normalized_value"] == (
        "2099-01-02"
    )
    assert extracted["fields"]["recent_revenue"]["normalized_value"] == 2000

    adapted = adapt_extracted_event(extracted)
    evidence_by_field = {
        evidence.field_name: evidence
        for evidence in adapted.evidence
    }
    expected = {
        "contract_amount": ("1,234", 1),
        "counterparty": ("테스트 상대방", 2),
        "contract_date": ("2099-01-02", 3),
    }
    for field_name, (raw_text, row_index) in expected.items():
        evidence = evidence_by_field[field_name]
        assert evidence.doc_id == "exchange_SYNTHETIC"
        assert evidence.raw_text == raw_text
        assert evidence.provenance is not None
        assert evidence.provenance.table_id == "XFormD1_Form0_Table0"
        assert evidence.provenance.row_index == row_index
        assert evidence.provenance.column_index == 1


@pytest.mark.parametrize(
    ("intended_group", "field", "canonical"),
    [
        ("계약(수주)일", "계약(수주)일", "contract_date"),
        ("계약내역", "계약금액 총액(원)", "contract_amount"),
        ("계약상대방", "계약상대방", "counterparty"),
    ],
)
def test_supply_aliases_do_not_use_field_only_fallback(
    intended_group: str,
    field: str,
    canonical: str,
) -> None:
    assert match_field("supply_contract", intended_group, field) == canonical
    assert match_field("supply_contract", "관련없는그룹", field) is None


@pytest.mark.parametrize(
    ("group", "field", "canonical"),
    [
        ("계약(수주)일자", "계약(수주)일자", "contract_date"),
        ("계약내역", "계약금액(원)", "contract_amount"),
        ("계약상대", "계약상대", "counterparty"),
        ("계약내역", "최근매출액(원)", "recent_revenue"),
        ("계약내역", "매출액대비(%)", "revenue_ratio"),
        ("계약기간", "시작일", "contract_start_date"),
        ("계약기간", "종료일", "contract_end_date"),
    ],
)
def test_existing_supply_current_table_fields_are_unchanged(
    group: str,
    field: str,
    canonical: str,
) -> None:
    assert match_field("supply_contract", group, field) == canonical


def _recent_revenue_row(*, group: str, value: str, row_index: int) -> dict:
    return {
        "group": group,
        "field": "최근매출액",
        "raw_field": "최근매출액(원)",
        "value": value,
        "unit": "원",
        "table_id": "table_synthetic",
        "row_index": row_index,
        "field_cell_index": 0,
        "value_cell_index": 1,
    }


@pytest.mark.parametrize("fallback_first", [True, False])
def test_exact_recent_revenue_wins_independently_of_row_order(
    fallback_first: bool,
) -> None:
    exact = _recent_revenue_row(group="계약내역", value="100", row_index=1)
    fallback = _recent_revenue_row(
        group="계약상대방",
        value="999",
        row_index=2,
    )
    rows = [fallback, exact] if fallback_first else [exact, fallback]
    document = {
        "metadata": {
            "doc_id": "exchange_SYNTHETIC",
            "corp_name": "테스트기업",
        },
        "title": "단일판매ㆍ공급계약 체결",
        "rows": rows,
        "correction": None,
    }

    extracted = extract_event(document)

    assert extracted["fields"]["recent_revenue"]["normalized_value"] == 100
    assert len(extracted["duplicate_fields"]) == 1
    assert extracted["duplicate_fields"][0]["canonical_field"] == "recent_revenue"
    assert extracted["duplicate_fields"][0]["normalized_value"] == 999


@pytest.mark.parametrize(
    ("group", "field"),
    [
        ("주요 계약조건", "주요 계약조건"),
        ("주요 계약조건", "조건부 계약내용"),
        ("계약내역", "계약내역"),
        ("관련공시", "※ 관련공시"),
    ],
)
def test_conditional_layout_containers_remain_unmapped(
    group: str,
    field: str,
) -> None:
    assert match_field("supply_contract", group, field) is None


def test_correction_current_body_alias_is_separate_from_xformd8(
    tmp_path: Path,
) -> None:
    document = parse_html(
        tmp_path,
        """
        <span>정정신고(보고)</span><span>단일판매ㆍ공급계약 체결</span>
        <table id="XFormD8_Form0_RepeatTable0">
          <tr><td>3. 정정사유</td><td>테스트 정정사유</td></tr>
          <tr><td>정정항목</td><td>정정전</td><td>정정후</td></tr>
          <tr><td>테스트 정정항목</td><td>정정 전</td><td>정정 후</td></tr>
        </table>
        <table id="XFormD1_Form0_Table0">
          <tr><td>2. 계약내역</td><td>최근매출액(원)</td><td>2,000</td></tr>
          <tr><td>계약금액 총액(원)</td><td>1,234</td></tr>
        </table>
        """,
    )

    extracted = extract_event(document)

    assert extracted["fields"]["contract_amount"]["normalized_value"] == 1234
    assert document["correction"]["reason"] == "테스트 정정사유"
    assert extracted["correction"]["changes"][0]["canonical_field"] is None
    assert extracted["correction"]["changes"][0]["before"] == "정정 전"
    assert extracted["correction"]["changes"][0]["after"] == "정정 후"
