"""Synthetic disclosure documents shared by integration and evaluation tests."""

import json
from pathlib import Path

DUMMY_REVENUE = "987654"
DUMMY_OTHER_REVENUE = "123456"
Q01_QUESTION = (
    "\ud55c\ubbf8\ubc18\ub3c4\uccb4 2025\ub144 "
    "\uc5f0\uacb0 \uae30\uc900 \ub9e4\ucd9c\uc561\uc774 \uc5bc\ub9c8\uc57c?"
)
Q02_QUESTION = (
    "\ud55c\ud654\uc624\uc158\uc774 2025\ub144 9\uc6d4\uc5d0 "
    "\uc591\ubc0d\ud574\uc6b4\uc774\ub791 \ub9fa\uc740 \uacf5\uae09\uacc4\uc57d, "
    "\uae08\uc561\uc774\ub791 \uacc4\uc57d\uae30\uac04 \uc54c\ub824\uc918."
)
Q13_QUESTION = (
    "\ud55c\ud654\uc624\uc158\uc758 2025\ub144 \uc591\ubc0d\ud574\uc6b4 \uacf5\uae09\uacc4\uc57d "
    "\uc6d0\uacf5\uc2dc \uc774\ud6c4 \uc815\uc815\ub41c "
    "\ubcc0\uacbd \uc774\ub825\uc744 \uc54c\ub824\uc918."
)
Q13_TITLE = "\ub2e8\uc77c\ud310\ub9e4\u318d\uacf5\uae09\uacc4\uc57d \uccb4\uacb0"


def write_q01_periodic_xml(
    corpus_dir: Path,
    *,
    filename: str = "dummy-periodic-source.xml",
    report_name: str = "\uc0ac\uc5c5\ubcf4\uace0\uc11c (2025)",
    tables: list[tuple[str, str, str, str]] | None = None,
) -> None:
    table_specs = tables or [
        (
            "\uc5f0\uacb0 \uc7ac\ubb34\uc81c\ud45c",
            "\uc5f0\uacb0 \uc190\uc775\uacc4\uc0b0\uc11c",
            "2025",
            DUMMY_REVENUE,
        )
    ]
    sections = "".join(
        f"""
    <SECTION-1>
      <TITLE>{section_title}</TITLE>
      <TABLE>
        <TITLE>{table_title}</TITLE>
        <P>(\ub2e8\uc704: dummy-unit)</P>
        <TR><TH>item</TH><TH>{header_year}</TH></TR>
        <TR><TD>\ub9e4\ucd9c\uc561</TD><TD>{revenue}</TD></TR>
      </TABLE>
    </SECTION-1>"""
        for section_title, table_title, header_year, revenue in table_specs
    )
    xml = f"""<?xml version="1.0" encoding="utf-8"?>
<DOCUMENT>
  <DOCUMENT-NAME>{report_name}</DOCUMENT-NAME>
  <COMPANY-NAME>\ud55c\ubbf8\ubc18\ub3c4\uccb4</COMPANY-NAME>
  <BODY>{sections}
  </BODY>
</DOCUMENT>
"""
    (corpus_dir / filename).write_text(xml, encoding="utf-8")


def write_q02_event_document(
    corpus_dir: Path,
    *,
    filename: str = "dummy-event-source.xml",
    title: str = "\ub2e8\uc77c\ud310\ub9e4\u318d\uacf5\uae09\uacc4\uc57d \uccb4\uacb0",
    amount: str = "7654321",
    unit: str = "dummy-currency",
    correction: bool = False,
) -> None:
    correction_markup = (
        """
  <span>\uc815\uc815\uc2e0\uace0(\ubcf4\uace0)</span>
  <table id="XFormD8">
    <tr><td>\uc815\uc815\uc77c\uc790</td><td>2025-09-20</td></tr>
  </table>"""
        if correction
        else ""
    )
    document = f"""<html>
<head><title>{title}</title></head>
<body>
  <span>{title}</span>
  {correction_markup}
  <table id="event-fields">
    <tr>
      <td>2. \uacc4\uc57d\ub0b4\uc5ed</td>
      <td>\uacc4\uc57d\uae08\uc561({unit})</td>
      <td>{amount}</td>
    </tr>
    <tr><td>\uacc4\uc57d\uae30\uac04</td><td>\uc2dc\uc791\uc77c</td><td>2025-09-01</td></tr>
    <tr><td>\uacc4\uc57d\uae30\uac04</td><td>\uc885\ub8cc\uc77c</td><td>2026-08-31</td></tr>
    <tr><td>3. \uacc4\uc57d\uc0c1\ub300</td><td>\uc591\ubc0d\ud574\uc6b4</td></tr>
  </table>
</body>
</html>
"""
    xml_path = corpus_dir / filename
    xml_path.write_text(document, encoding="utf-8")
    metadata = {
        "corp_name": "\ud55c\ud654\uc624\uc158",
        "report_name": "\ub2e8\uc77c\ud310\ub9e4\u318d\uacf5\uae09\uacc4\uc57d \uccb4\uacb0",
        "receipt_date": "2025-09-15",
    }
    xml_path.with_suffix(".meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )


def write_q13_event_document(
    corpus_dir: Path,
    *,
    filename: str,
    receipt_date: str,
    amount: str,
    end_date: str,
    correction: bool = False,
    original_filing_date: str | None = None,
    chain_complete: bool | None = None,
    related_disclosures: list[dict[str, str]] | None = None,
) -> None:
    correction_markup = ""
    if correction:
        correction_markup = f"""
  <span>\uc815\uc815\uc2e0\uace0(\ubcf4\uace0)</span>
  <table id="XFormD8-correction">
    <tr><td>\uc815\uc815\uc77c\uc790</td><td>{receipt_date}</td></tr>
    <tr>
      <td>\uc815\uc815\uad00\ub828 \uacf5\uc2dc\uc11c\ub958\uc81c\ucd9c\uc77c</td>
      <td>{original_filing_date or ''}</td>
    </tr>
    <tr><td>\uc815\uc815\uc0ac\uc720</td><td>dummy correction reason</td></tr>
    <tr><td>\uc815\uc815\ud56d\ubaa9</td><td>\uc815\uc815\uc804</td><td>\uc815\uc815\ud6c4</td></tr>
    <tr><td>\uacc4\uc57d\uae08\uc561</td><td>100</td><td>{amount}</td></tr>
    <tr>
      <td>\uacc4\uc57d\uae30\uac04 \uc885\ub8cc\uc77c</td>
      <td>2026-01-01</td><td>{end_date}</td>
    </tr>
  </table>"""

    document = f"""<html>
<head><title>{Q13_TITLE}</title></head>
<body>
  <span>{Q13_TITLE}</span>
  {correction_markup}
  <table id="event-fields">
    <tr>
      <td>2. \uacc4\uc57d\ub0b4\uc5ed</td>
      <td>\uacc4\uc57d\uae08\uc561(dummy-currency)</td><td>{amount}</td>
    </tr>
    <tr><td>\uacc4\uc57d\uae30\uac04</td><td>\uc2dc\uc791\uc77c</td><td>2025-09-01</td></tr>
    <tr><td>\uacc4\uc57d\uae30\uac04</td><td>\uc885\ub8cc\uc77c</td><td>{end_date}</td></tr>
    <tr><td>3. \uacc4\uc57d\uc0c1\ub300</td><td>\uc591\ubc0d\ud574\uc6b4</td></tr>
  </table>
</body>
</html>
"""
    xml_path = corpus_dir / filename
    xml_path.write_text(document, encoding="utf-8")
    metadata: dict[str, object] = {
        "corp_name": "\ud55c\ud654\uc624\uc158",
        "report_name": Q13_TITLE,
        "receipt_date": receipt_date,
    }
    if chain_complete is not None:
        metadata["chain_complete"] = chain_complete
    if related_disclosures is not None:
        metadata["related_disclosures"] = related_disclosures
    xml_path.with_suffix(".meta.json").write_text(
        json.dumps(metadata, ensure_ascii=False),
        encoding="utf-8",
    )
