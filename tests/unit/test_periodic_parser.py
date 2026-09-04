from pathlib import Path

import pytest

from disclosure_agent.periodic.parser import (
    parse_periodic_xml,
    sanitize_dart_xml_text,
)


def parse_xml(tmp_path: Path, body: str) -> dict:
    xml_path = tmp_path / "SAMPLE.xml"
    xml_path.write_text(
        f"""<DOCUMENT>
        <DOCUMENT-NAME>테스트보고서</DOCUMENT-NAME>
        <COMPANY-NAME>테스트기업</COMPANY-NAME>
        <BODY>{body}</BODY>
        </DOCUMENT>""",
        encoding="utf-8",
    )
    return parse_periodic_xml(xml_path)


def test_table_group_unit_attributes_and_provenance(tmp_path: Path) -> None:
    parsed = parse_xml(
        tmp_path,
        """
        <SECTION-1>
          <TITLE>재무 정보</TITLE>
          <TABLE-GROUP ACLASS="TEST_GROUP" ADELETETABLE="N">
            <TABLE><TR><TD>(단위 : 백만원)</TD></TR></TABLE>
            <TABLE><TR><TD>매출액</TD><TE ACODE="REV">100</TE></TR></TABLE>
          </TABLE-GROUP>
        </SECTION-1>
        """,
    )

    group = parsed["table_groups"][0]
    assert group["aclass"] == "TEST_GROUP"
    assert group["adeletetable"] == "N"
    assert group["unit"] == "(단위 : 백만원)"
    assert len(group["tables"]) == 2
    assert [table["table_id"] for table in group["tables"]] == [
        table["table_id"] for table in parsed["tables"]
    ]
    assert group["tables"][1]["unit"] == "(단위 : 백만원)"
    assert group["tables"][1]["own_unit"] is None

    cell = group["tables"][1]["rows"][0][1]
    assert cell["type"] == "TE"
    assert cell["provenance"] == {
        "section_id": "section_0001",
        "table_group_id": "table_group_0001",
        "table_id": "table_0002",
        "row_index": 0,
        "col_index": 1,
    }


def test_cell_raw_attributes_are_preserved(tmp_path: Path) -> None:
    parsed = parse_xml(
        tmp_path,
        """
        <SECTION-1><TABLE><TR><TE ACODE="TEST" ACONTEXT="SAMPLE_CONTEXT"
        ANEGATED="Y" AUNIT="WON" AUNITVALUE="1">100</TE></TR></TABLE></SECTION-1>
        """,
    )
    cell = parsed["tables"][0]["rows"][0][0]
    raw_attribute_keys = ("acode", "acontext", "anegated", "aunit", "aunit_value")
    assert {key: cell[key] for key in raw_attribute_keys} == {
        "acode": "TEST",
        "acontext": "SAMPLE_CONTEXT",
        "anegated": "Y",
        "aunit": "WON",
        "aunit_value": "1",
    }


def test_standalone_table_has_no_group_and_preserves_cell_types(tmp_path: Path) -> None:
    parsed = parse_xml(
        tmp_path,
        """<SECTION-1><TABLE><TR><TH>H</TH><TD>D</TD><TE>E</TE><TU>U</TU></TR>
        </TABLE></SECTION-1>""",
    )
    table = parsed["tables"][0]
    assert table["table_group_id"] is None
    assert [cell["type"] for cell in table["rows"][0]] == ["TH", "TD", "TE", "TU"]
    assert table["rows"][0][0]["provenance"]["table_group_id"] is None


def test_library_contents_are_not_lost_or_duplicated(tmp_path: Path) -> None:
    parsed = parse_xml(
        tmp_path,
        """
        <SECTION-1><TITLE>상위</TITLE><LIBRARY><P>문단 <SPAN>내용</SPAN></P>
          <TABLE-GROUP ACLASS="LIB_GROUP"><TABLE><TR><TD>값</TD></TR></TABLE></TABLE-GROUP>
        </LIBRARY></SECTION-1>
        """,
    )
    assert parsed["sections"][0]["paragraphs"] == ["문단 내용"]
    assert len(parsed["table_groups"]) == 1
    assert len(parsed["tables"]) == 1


def test_nested_section_hierarchy_regression(tmp_path: Path) -> None:
    parsed = parse_xml(
        tmp_path,
        """
        <SECTION-1><TITLE>상위</TITLE><P>상위 본문</P>
          <SECTION-2><TITLE>하위</TITLE><P>하위 본문</P></SECTION-2>
        </SECTION-1>
        """,
    )
    parent, child = parsed["sections"]
    assert parent["paragraphs"] == ["상위 본문"]
    assert child["parent_section_id"] == parent["section_id"]
    assert child["section_path"] == ["상위", "하위"]
    assert child["paragraphs"] == ["하위 본문"]


def test_malformed_text_tokens_do_not_break_following_sections(tmp_path: Path) -> None:
    parsed = parse_xml(
        tmp_path,
        """
        <SECTION-1><TITLE>앞 절</TITLE>
          <P>S&amp;P는 정상 entity이고 S&P와 <프로젝트>는 malformed 원문이다.</P>
        </SECTION-1>
        <SECTION-1><TITLE>III. 재무에 관한 사항</TITLE><P>후속 본문</P></SECTION-1>
        """,
    )

    assert len(parsed["sections"]) == 2
    assert parsed["sections"][0]["text"] == (
        "S&P는 정상 entity이고 S&P와 <프로젝트>는 malformed 원문이다."
    )
    assert parsed["sections"][1]["title"] == "III. 재무에 관한 사항"


def _comparison_document(comparison: str) -> str:
    return f"""<DOCUMENT>
      <DOCUMENT-NAME>합성 정기보고서</DOCUMENT-NAME>
      <COMPANY-NAME>테스트기업</COMPANY-NAME>
      <BODY>
        <SECTION-1>
          <TITLE>합성 재무정보</TITLE>
          <P>비율 기준 {comparison}</P>
          <TABLE-GROUP ACLASS="{{XBRL}}IS_C1" ADELETETABLE="N">
            <TITLE>연결재무제표</TITLE>
            <TABLE>
              <TR><TD>(단위 : 백만원)</TD></TR>
              <TR><TH>항목</TH><TH>제 1 기 2099</TH></TR>
              <TR>
                <TD>매출액</TD>
                <TE ACODE="ifrs-full_Revenue"
                    ACONTEXT="CFY2099dFY_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_ifrs-full_ConsolidatedMember"
                    AUNIT="KRW">123</TE>
              </TR>
            </TABLE>
          </TABLE-GROUP>
        </SECTION-1>
      </BODY>
    </DOCUMENT>"""


def _parse_comparison_document(
    tmp_path: Path,
    *,
    filename: str,
    comparison: str,
) -> dict:
    source = tmp_path / filename
    source.write_text(_comparison_document(comparison), encoding="utf-8")
    return parse_periodic_xml(source)


def test_malformed_percent_comparison_matches_valid_equivalent_with_provenance(
    tmp_path: Path,
) -> None:
    malformed = _parse_comparison_document(
        tmp_path,
        filename="MALFORMED.xml",
        comparison="< 10%",
    )
    valid = _parse_comparison_document(
        tmp_path,
        filename="VALID.xml",
        comparison="&lt; 10%",
    )

    assert malformed["sections"] == valid["sections"]
    assert malformed["table_groups"] == valid["table_groups"]
    assert malformed["tables"] == valid["tables"]
    assert malformed["sections"][0]["text"] == "비율 기준 < 10%"

    group = malformed["table_groups"][0]
    table = malformed["tables"][0]
    cell = table["rows"][2][1]
    assert group["title"] == "연결재무제표"
    assert group["aclass"] == "{XBRL}IS_C1"
    assert "합성 재무정보" in group["raw_context"]
    assert table["title"] is None
    assert table["unit"] == "(단위 : 백만원)"
    assert table["table_id"] == "table_0001"
    assert table["table_group_id"] == "table_group_0001"
    assert table["section_path"] == ["합성 재무정보"]
    assert [item["text"] for item in table["rows"][1]] == [
        "항목",
        "제 1 기 2099",
    ]
    assert table["rows"][2][0]["text"] == "매출액"
    assert cell == {
        "type": "TE",
        "text": "123",
        "provenance": {
            "section_id": "section_0001",
            "table_group_id": "table_group_0001",
            "table_id": "table_0001",
            "row_index": 2,
            "col_index": 1,
        },
        "acode": "ifrs-full_Revenue",
        "acontext": (
            "CFY2099dFY_"
            "ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis_"
            "ifrs-full_ConsolidatedMember"
        ),
        "aunit": "KRW",
    }


def test_valid_percent_comparison_is_sanitizer_noop() -> None:
    valid_xml = _comparison_document("&lt; 10%")

    assert sanitize_dart_xml_text(valid_xml) == valid_xml


def test_repeated_malformed_percent_comparisons_preserve_order(
    tmp_path: Path,
) -> None:
    parsed = parse_xml(
        tmp_path,
        """
        <SECTION-1>
          <TITLE>비교 기준</TITLE>
          <P>첫째 < 10%, 둘째 < 5%</P>
          <P>후속 문단</P>
        </SECTION-1>
        """,
    )

    assert parsed["sections"][0]["paragraphs"] == [
        "첫째 < 10%, 둘째 < 5%",
        "후속 문단",
    ]


@pytest.mark.parametrize(
    "body",
    [
        "<SECTION-1><P>미지원 비교 <10%</P></SECTION-1>",
        "<SECTION-1><P>모호한 비교 < 10 단위</P></SECTION-1>",
        "<SECTION-1><P>닫힘 불일치</SECTION-1>",
        "<SECTION-1><P>잘린 문서",
        "<SECTION-1><TABLE><TR><TD>중첩 오류</TR></TABLE></SECTION-1>",
        '<SECTION-1 BAD=="VALUE"><P>속성 오류</P></SECTION-1>',
    ],
)
def test_unrelated_or_ambiguous_malformed_xml_still_fails_closed(
    tmp_path: Path,
    body: str,
) -> None:
    with pytest.raises(ValueError, match="XML 파싱에 실패했습니다"):
        parse_xml(tmp_path, body)
