"""이벤트공시 XML Parser 모듈.

역할:
- 공급계약과 신규시설투자 공시의 구조 및 필드 후보를 보존한다.

예정 입력:
- XML 파일 경로
- DocumentMetadata

예정 출력:
- ParsedDocument와 Event 후보

TODO:
- Pilot XML별 태그와 표 구조 분석
- 이벤트 유형별 필드 매핑 구현
- 값, 단위, 날짜의 원문 표현 보존
"""

"""이벤트공시 XML Parser 모듈.

역할:
- 공급계약과 신규시설투자 등 거래소공시의 표 구조를 보존한다.
- 정정공시의 정정일자, 정정사유, 정정 전/후 값을 보존한다.
"""

import json
import re
from pathlib import Path
from typing import Any

from bs4 import BeautifulSoup, Tag


UNIT_PATTERN = re.compile(r"^(.*)\(([^()]*)\)$")


def clean_text(text: str | None) -> str:
    """공백과 줄바꿈을 정리한다."""
    if not text:
        return ""

    return " ".join(text.split()).strip()


def element_text(element: Tag) -> str:
    """
    td/span 내부의 모든 텍스트를 합친다.
    <br>이나 중첩 span이 있어도 텍스트를 보존한다.
    """
    return clean_text(element.get_text(" ", strip=True))


def strip_number_prefix(text: str) -> str:
    """
    '2. 계약내역' -> '계약내역'
    '3. 계약상대' -> '계약상대'
    '- 체결계약명' -> '체결계약명'
    """
    text = text.strip()

    text = re.sub(r"^\d+\.\s*", "", text)
    text = re.sub(r"^-\s*", "", text)

    return text.strip()


def split_unit(field_text: str) -> tuple[str, str | None]:
    """
    '계약금액(원)' -> ('계약금액', '원')
    '매출액대비(%)' -> ('매출액대비', '%')
    """
    match = UNIT_PATTERN.match(field_text.strip())

    if not match:
        return field_text.strip(), None

    field = match.group(1).strip()
    unit = match.group(2).strip()

    return field, unit


def extract_title(root: BeautifulSoup) -> str | None:
    """HTML <title>의 내용을 추출한다."""
    title = root.find("title")

    if title is None:
        return None

    return element_text(title)


def find_main_title(root: BeautifulSoup) -> str | None:
    """
    본문의 실제 공시 제목을 찾는다.
    예: 단일판매ㆍ공급계약 체결
    """
    for span in root.find_all("span"):
        text = element_text(span)

        if "단일판매" in text and "공급계약" in text:
            return text

    return extract_title(root)


def is_correction_document(root: BeautifulSoup) -> bool:
    """'정정신고(보고)' 영역이 존재하는지 확인한다."""
    for span in root.find_all("span"):
        if "정정신고" in element_text(span):
            return True

    return False


def parse_normal_rows(root: BeautifulSoup) -> list[dict[str, Any]]:
    """
    일반 거래소공시 표를 row 구조로 변환한다.

    예:
    {
        "group": "계약내역",
        "field": "계약금액",
        "raw_field": "계약금액(원)",
        "value": "22,764,764,160,000",
        "unit": "원"
    }
    """

    rows: list[dict[str, Any]] = []
    current_group: str | None = None

    for table in root.find_all("table"):
        table_id = table.get("id", "")

        # 정정 정보 전용 표 제외
        if "XFormD8" in table_id:
            continue

        for tr in table.find_all("tr"):
            # 중첩 table의 td까지 가져오지 않도록 현재 tr의 직접 자식만
            cells = tr.find_all("td", recursive=False)

            if len(cells) < 2:
                continue

            texts = [element_text(cell) for cell in cells]
            texts = [text for text in texts if text]

            if len(texts) < 2:
                continue

            # 예:
            # 계약내역 | 계약금액(원) | 22,764...
            if len(texts) >= 3:
                possible_group = strip_number_prefix(texts[0])
                raw_field = texts[-2]
                value = texts[-1]

                current_group = possible_group

            # 예:
            # 시작일 | 2025-07-24
            # 또는
            # 3. 계약상대 | 글로벌 대형기업
            else:
                raw_field = texts[0]
                value = texts[1]

                if re.match(r"^\d+\.", raw_field.strip()):
                    current_group = strip_number_prefix(raw_field)
                    raw_field = strip_number_prefix(raw_field)

            field_text = strip_number_prefix(raw_field)
            field, unit = split_unit(field_text)

            rows.append(
                {
                    "group": current_group,
                    "field": field,
                    "raw_field": raw_field,
                    "value": value,
                    "unit": unit,
                }
            )

    return rows


def parse_correction(root: BeautifulSoup) -> dict[str, Any] | None:
    """정정공시 앞부분의 정정 정보를 구조화한다."""

    if not is_correction_document(root):
        return None

    correction_date = None
    original_filing_date = None
    reason = None
    changes: list[dict[str, Any]] = []

    for table in root.find_all("table"):
        table_id = table.get("id", "")

        if "XFormD8" not in table_id:
            continue

        for tr in table.find_all("tr"):
            cells = tr.find_all("td", recursive=False)

            texts = [element_text(cell) for cell in cells]
            texts = [text for text in texts if text]

            if not texts:
                continue

            # 정정일자
            if "정정일자" in texts[0] and len(texts) >= 2:
                correction_date = texts[-1]
                continue

            # 원본 제출일
            if (
                "정정관련 공시서류제출일" in texts[0]
                and len(texts) >= 2
            ):
                original_filing_date = texts[-1]
                continue

            # 정정사유
            if "정정사유" in texts[0] and len(texts) >= 2:
                reason = texts[-1]
                continue

            # 정정항목 / 정정전 / 정정후 header
            if (
                len(texts) >= 3
                and texts[0] == "정정항목"
                and texts[1] == "정정전"
                and texts[2] == "정정후"
            ):
                continue

            # 실제 변경 행
            if len(texts) >= 3:
                raw_field = texts[0]
                before = texts[1]
                after = texts[2]

                # 정정 안내용 행 제외
                if raw_field.startswith("1. 정정관련"):
                    continue

                if raw_field.startswith("2. 정정관련"):
                    continue

                if raw_field.startswith("3. 정정사유"):
                    continue

                if raw_field.startswith("4. 정정사항"):
                    continue

                changes.append(
                    {
                        "raw_field": raw_field,
                        "field": strip_number_prefix(raw_field),
                        "before": before,
                        "after": after,
                    }
                )

    return {
        "correction_date": correction_date,
        "original_filing_date": original_filing_date,
        "reason": reason,
        "changes": changes,
    }


def parse_exchange_document(
    xml_path: Path,
    corp_name: str | None = None,
    report_name: str | None = None,
    receipt_date: str | None = None,
) -> dict[str, Any]:
    """거래소공시 파일 한 건을 ParsedDocument 형태로 변환한다."""

    # 거래소공시는 확장자가 xml이어도 실질적으로 HTML 구조이므로
    # 엄격한 XML parser 대신 html.parser 사용
    raw_data = xml_path.read_bytes()
    root = BeautifulSoup(raw_data, "html.parser")

    receipt_no = xml_path.stem

    correction = parse_correction(root)

    return {
        "metadata": {
            "doc_id": f"exchange_{receipt_no}",
            "corp_name": corp_name,
            "report_name": report_name,
            "receipt_date": receipt_date,
            "document_type": "exchange",
            "source_file": xml_path.name,
            "is_correction": correction is not None,
        },
        "title": find_main_title(root),
        "rows": parse_normal_rows(root),
        "correction": correction,
    }


def save_parsed_document(
    document: dict[str, Any],
    output_path: Path,
) -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    with output_path.open("w", encoding="utf-8") as f:
        json.dump(
            document,
            f,
            ensure_ascii=False,
            indent=2,
        )

    print(f"saved to: {output_path}")
    print(f"rows: {len(document['rows'])}")
    print(
        "is_correction:",
        document["metadata"]["is_correction"],
    )


if __name__ == "__main__":
    input_path = Path(
        "data/raw/exchange/20230317800146.xml"
    )

    output_path = Path(
        "data/processed/exchange_20230317800146_parsed.json"
    )

    document = parse_exchange_document(
        input_path,
        corp_name="삼성바이오로직스",
        report_name="신규시설투자등",
        receipt_date="2023-03-17",
    )

    save_parsed_document(
        document,
        output_path,
    )