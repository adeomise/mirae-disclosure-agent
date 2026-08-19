from pathlib import Path

from disclosure_agent.periodic.parser import parse_periodic_xml


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
