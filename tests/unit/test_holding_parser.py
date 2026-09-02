from __future__ import annotations

from pathlib import Path

from disclosure_agent.holding.parser import parse_holding_xml


def test_holding_parser_reuses_strict_sanitized_xml_path(tmp_path: Path) -> None:
    path = tmp_path / "holding.xml"
    path.write_text(
        """<?xml version="1.0" encoding="utf-8"?>
        <DOCUMENT><BODY><SECTION-1><P>테스트 A & B</P><TABLE><TBODY><TR>
          <TD>현재</TD><TE ACODE="THS_STK_CNT">1,000</TE>
          <TE ACODE="THS_STK_RT">20.5</TE>
        </TR></TBODY></TABLE></SECTION-1></BODY></DOCUMENT>
        """,
        encoding="utf-8",
    )

    document = parse_holding_xml(path, doc_id="holding_SYNTHETIC")

    assert document["metadata"] == {
        "doc_id": "holding_SYNTHETIC",
        "document_type": "holding",
        "source_path": str(path),
    }
    row = document["tables"][0]["rows"][0]
    assert [(cell.get("acode"), cell["text"]) for cell in row] == [
        (None, "현재"),
        ("THS_STK_CNT", "1,000"),
        ("THS_STK_RT", "20.5"),
    ]
    assert row[1]["provenance"] == {
        "table_id": "table_0001",
        "row_index": 0,
        "column_index": 1,
    }


def test_holding_parser_requires_holding_doc_identity(tmp_path: Path) -> None:
    path = tmp_path / "holding.xml"
    path.write_text("<DOCUMENT/>", encoding="utf-8")

    try:
        parse_holding_xml(path, doc_id=" ")
    except ValueError as error:
        assert "doc_id" in str(error)
    else:
        raise AssertionError("empty holding doc_id must fail")
