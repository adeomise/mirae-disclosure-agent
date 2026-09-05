from pathlib import Path

import pytest
from lxml import etree

from disclosure_agent.periodic.parser import (
    _repair_malformed_percent_comparisons_in_text,
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


def _incomplete_korean_text_document(fragment: str) -> str:
    return f"""<DOCUMENT>
      <DOCUMENT-NAME>합성 정기보고서</DOCUMENT-NAME>
      <COMPANY-NAME>테스트기업</COMPANY-NAME>
      <BODY>
        <SECTION-1>
          <TITLE>합성 재무정보</TITLE>
          <P>미완성 표기 {fragment}</P>
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


def _attribute_comparison_document(
    *,
    attribute_value: str,
    cell_text: str,
) -> str:
    return f"""<DOCUMENT>
      <DOCUMENT-NAME>합성 정기보고서</DOCUMENT-NAME>
      <COMPANY-NAME>테스트기업</COMPANY-NAME>
      <BODY><SECTION-1><TABLE><TR>
        <TD ATEST="{attribute_value}">{cell_text}</TD>
      </TR></TABLE></SECTION-1></BODY>
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


def test_malformed_percent_comparison_in_attribute_remains_fail_closed(
    tmp_path: Path,
) -> None:
    malformed = _attribute_comparison_document(
        attribute_value="< 10%",
        cell_text="값",
    )

    assert _repair_malformed_percent_comparisons_in_text(malformed) == malformed
    assert 'ATEST="&lt; 10%">' not in sanitize_dart_xml_text(malformed)

    source = tmp_path / "MALFORMED_ATTRIBUTE.xml"
    source.write_text(malformed, encoding="utf-8")
    with pytest.raises(ValueError, match="XML 파싱에 실패했습니다"):
        parse_periodic_xml(source)


def test_valid_escaped_percent_comparison_in_attribute_is_noop() -> None:
    valid = _attribute_comparison_document(
        attribute_value="&lt; 10%",
        cell_text="값",
    )

    assert sanitize_dart_xml_text(valid) == valid
    root = etree.fromstring(valid.encode("utf-8"))
    assert root.find(".//TD").get("ATEST") == "< 10%"


def test_quoted_gt_does_not_end_attribute_context_early(tmp_path: Path) -> None:
    boundary_probe = _attribute_comparison_document(
        attribute_value="synthetic > < 10%",
        cell_text="value < 10%",
    )
    repaired_probe = _repair_malformed_percent_comparisons_in_text(boundary_probe)
    assert 'ATEST="synthetic > < 10%"' in repaired_probe
    assert ">value &lt; 10%" in repaired_probe

    malformed_text = _attribute_comparison_document(
        attribute_value="synthetic > text",
        cell_text="value < 10%",
    )
    sanitized = sanitize_dart_xml_text(malformed_text)

    assert 'ATEST="synthetic > text"' in sanitized
    assert "value &lt; 10%" in sanitized

    source = tmp_path / "QUOTED_GT.xml"
    source.write_text(malformed_text, encoding="utf-8")
    parsed = parse_periodic_xml(source)
    assert parsed["tables"][0]["rows"][0][0]["text"] == "value < 10%"


def test_percent_comparison_in_protected_regions_is_noop() -> None:
    protected = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><?audit threshold="< 10%"?>
      <DOCUMENT-NAME>합성 정기보고서</DOCUMENT-NAME>
      <COMPANY-NAME>테스트기업</COMPANY-NAME>
      <BODY><!-- threshold < 10% -->
        <SECTION-1><P><![CDATA[threshold < 10%]]></P></SECTION-1>
      </BODY>
    </DOCUMENT>"""

    assert sanitize_dart_xml_text(protected) == protected


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


@pytest.mark.parametrize("horizontal_whitespace", ["", " \t "])
def test_incomplete_korean_text_matches_valid_equivalent_with_provenance(
    tmp_path: Path,
    horizontal_whitespace: str,
) -> None:
    malformed_source = _incomplete_korean_text_document(
        f"<가상{horizontal_whitespace}"
    )
    valid_source = _incomplete_korean_text_document(
        f"&lt;가상{horizontal_whitespace}"
    )
    malformed_path = tmp_path / "MALFORMED_KOREAN_TEXT.xml"
    valid_path = tmp_path / "VALID_KOREAN_TEXT.xml"
    malformed_path.write_text(malformed_source, encoding="utf-8")
    valid_path.write_text(valid_source, encoding="utf-8")

    malformed = parse_periodic_xml(malformed_path)
    valid = parse_periodic_xml(valid_path)

    assert sanitize_dart_xml_text(malformed_source) == valid_source
    assert sanitize_dart_xml_text(valid_source) == valid_source
    assert malformed["sections"] == valid["sections"]
    assert malformed["table_groups"] == valid["table_groups"]
    assert malformed["tables"] == valid["tables"]
    assert malformed["sections"][0]["text"] == "미완성 표기 <가상"

    cell = malformed["tables"][0]["rows"][2][1]
    assert cell["acode"] == "ifrs-full_Revenue"
    assert cell["acontext"].startswith("CFY2099dFY_")
    assert cell["aunit"] == "KRW"
    assert cell["provenance"] == {
        "section_id": "section_0001",
        "table_group_id": "table_group_0001",
        "table_id": "table_0001",
        "row_index": 2,
        "col_index": 1,
    }


@pytest.mark.parametrize(
    "body",
    [
        '<SECTION-1><P ATEST="<가상">본문</P></SECTION-1>',
        "<SECTION-1><P ATEST=<가상</P></SECTION-1>",
        "<SECTION-1><P>미지원 <ASCII_TOKEN</P></SECTION-1>",
        "<SECTION-1><P>미지원 <가상1</P></SECTION-1>",
        "<SECTION-1><P>미지원 <가A</P></SECTION-1>",
        "<SECTION-1><P>미지원 <가나다라마바사아자차</P></SECTION-1>",
        "<SECTION-1><P>미지원 <</P></SECTION-1>",
        "<SECTION-1><P>미지원 <가상</UNKNOWN></P></SECTION-1>",
        "<SECTION-1><P>미지원 <가상\n</P></SECTION-1>",
    ],
)
def test_incomplete_korean_text_boundary_cases_remain_fail_closed(
    tmp_path: Path,
    body: str,
) -> None:
    with pytest.raises(ValueError, match="XML 파싱에 실패했습니다"):
        parse_xml(tmp_path, body)


def test_quoted_gt_preserves_korean_text_repair_boundary(tmp_path: Path) -> None:
    malformed = _attribute_comparison_document(
        attribute_value="synthetic > text",
        cell_text="본문 <가상",
    )
    sanitized = sanitize_dart_xml_text(malformed)

    assert 'ATEST="synthetic > text"' in sanitized
    assert ">본문 &lt;가상</TD>" in sanitized

    source = tmp_path / "QUOTED_GT_KOREAN_TEXT.xml"
    source.write_text(malformed, encoding="utf-8")
    parsed = parse_periodic_xml(source)
    assert parsed["tables"][0]["rows"][0][0]["text"] == "본문 <가상"


def test_incomplete_korean_text_in_protected_regions_is_noop() -> None:
    protected = """<?xml version="1.0" encoding="UTF-8"?>
    <DOCUMENT><?audit token="<가상</P>"?>
      <DOCUMENT-NAME>합성 정기보고서</DOCUMENT-NAME>
      <COMPANY-NAME>테스트기업</COMPANY-NAME>
      <BODY><!-- token <가상</P> -->
        <SECTION-1><P><![CDATA[token <가상</P>]]></P></SECTION-1>
      </BODY>
    </DOCUMENT>"""

    assert sanitize_dart_xml_text(protected) == protected


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
