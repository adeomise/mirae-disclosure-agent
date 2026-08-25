from pathlib import Path

from disclosure_agent.event.parser import parse_exchange_document


def parse_html(tmp_path: Path, body: str) -> dict:
    source = tmp_path / "SAMPLE.xml"
    source.write_text(f"<html><body>{body}</body></html>", encoding="utf-8")
    return parse_exchange_document(source, corp_name="테스트기업")


def test_supply_contract_rows_preserve_values_units_and_provenance(tmp_path: Path) -> None:
    document = parse_html(
        tmp_path,
        """
        <span>단일판매ㆍ공급계약 체결</span>
        <table id="XFormD1_Form0_Table0">
          <tr><td>2. 계약내역</td><td>계약금액(원)</td><td>1,000</td></tr>
          <tr><td>※ 관련공시</td><td>-</td></tr>
        </table>
        """,
    )

    contract = document["rows"][0]
    assert contract == {
        "group": "계약내역",
        "field": "계약금액",
        "raw_field": "계약금액(원)",
        "value": "1,000",
        "unit": "원",
        "table_id": "XFormD1_Form0_Table0",
        "row_index": 0,
        "field_cell_index": 1,
        "value_cell_index": 2,
    }
    assert document["related_disclosures"] == [
        {
            "raw_field": "※ 관련공시",
            "value": "-",
            "table_id": "XFormD1_Form0_Table0",
            "row_index": 1,
            "field_cell_index": 0,
            "value_cell_index": 1,
        }
    ]


def test_correction_preserves_raw_reference_changes_and_provenance(tmp_path: Path) -> None:
    document = parse_html(
        tmp_path,
        """
        <span>정정신고(보고)</span><span>단일판매ㆍ공급계약 체결</span>
        <table id="XFormD8_Form0_Table1"><tr><td>정정일자</td><td>YYYY-MM-DD</td></tr></table>
        <table id="XFormD8_Form0_RepeatTable0">
          <tr><td>1. 정정관련 공시서류</td><td>단일판매ㆍ공급계약 체결</td></tr>
          <tr><td>2. 정정관련 공시서류제출일</td><td>YYYY-MM-DD</td></tr>
          <tr><td>3. 정정사유</td><td>정정 사유 원문</td></tr>
          <tr><td>정정항목</td><td>정정전</td><td>정정후</td></tr>
          <tr><td>3. 계약상대</td><td>정정 전 값</td><td>정정 후 값</td></tr>
        </table>
        """,
    )

    correction = document["correction"]
    assert correction["correction_date"] == "YYYY-MM-DD"
    assert correction["original_report_name"] == "단일판매ㆍ공급계약 체결"
    assert correction["original_filing_date"] == "YYYY-MM-DD"
    assert correction["reason"] == "정정 사유 원문"
    assert correction["changes"] == [
        {
            "raw_field": "3. 계약상대",
            "field": "계약상대",
            "before": "정정 전 값",
            "after": "정정 후 값",
            "table_id": "XFormD8_Form0_RepeatTable0",
            "row_index": 4,
            "field_cell_index": 0,
            "before_cell_index": 1,
            "after_cell_index": 2,
        }
    ]


def test_facility_investment_keeps_existing_row_shape_with_fallback_table_id(
    tmp_path: Path,
) -> None:
    document = parse_html(
        tmp_path,
        """
        <span>신규 시설투자 등</span>
        <table><tr><td>2. 투자내역</td><td>투자금액(원)</td><td>2,000</td></tr></table>
        """,
    )

    investment = document["rows"][0]
    assert investment["group"] == "투자내역"
    assert investment["field"] == "투자금액"
    assert investment["raw_field"] == "투자금액(원)"
    assert investment["unit"] == "원"
    assert investment["value"] == "2,000"
    assert investment["unit"] == "원"
    assert investment["table_id"] == "table_0001"
    assert investment["row_index"] == 0

def test_parser_does_not_treat_parenthetical_label_as_unit(
    tmp_path: Path,
) -> None:
    document = parse_html(
        tmp_path,
        """
        <span>신규 시설투자 등</span>
        <table>
          <tr>
            <td>이사회결의일(결정일)</td>
            <td>2025-03-01</td>
          </tr>
        </table>
        """,
    )

    row = next(
        row
        for row in document["rows"]
        if row["raw_field"] == "이사회결의일(결정일)"
    )

    assert row["raw_field"] == "이사회결의일(결정일)"
    assert row["field"] == "이사회결의일(결정일)"
    assert row["unit"] is None
    assert row["value"] == "2025-03-01"