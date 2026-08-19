"""Q01 vertical slice 테스트 공용 fixture.

주의:
    여기의 synthetic fixture는 Parser의 최종 Data Contract를 확정하는 것이 아니다.
    Q01 downstream 코드가 동작하기 위해 필요한 '최소 입력 조건'을 고정해 두는 용도다.
    docs/data_contracts.md 확정 시 필드 이름과 계층은 바뀔 수 있다.

    실제 공시 수치·접수번호는 사용하지 않는다. 모든 값은 가공된 예시다.
"""

from __future__ import annotations

import copy
import json
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

FIXTURE_DIR = Path(__file__).parent / "fixtures"

SYNTHETIC_DOCUMENT_PATH = FIXTURE_DIR / "synthetic_parsed_document.json"

SYNTHETIC_AMBIGUOUS_DOCUMENT_PATH = FIXTURE_DIR / "synthetic_ambiguous_document.json"

# 합성 manifest. 실제 접수번호를 쓰지 않는다.
SYNTHETIC_MANIFEST_RECORDS: list[dict[str, Any]] = [
    {
        "doc_id": "periodic_SYNTHETIC0001",
        "corp_code": "00000001",
        "corp_name": "테스트반도체(주)",
        "stock_code": "000001",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "사업보고서 (2025.12)",
        "is_correction": False,
        "rcept_no": "SYNTHETIC0001",
        "rcept_dt": "20260312",
        "base_year": 2025,
        "base_month": 12,
        "file_path": "raw/periodic/테스트반도체/SYNTHETIC0001_annual_2025_12",
        "file_format": "xml",
        "n_files": 1,
    },
    {
        # 같은 기업의 이전 연도. 연도 필터가 걸러야 한다.
        "doc_id": "periodic_SYNTHETIC0002",
        "corp_code": "00000001",
        "corp_name": "테스트반도체(주)",
        "stock_code": "000001",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "사업보고서 (2024.12)",
        "is_correction": False,
        "rcept_no": "SYNTHETIC0002",
        "rcept_dt": "20250313",
        "base_year": 2024,
        "base_month": 12,
        "file_path": "raw/periodic/테스트반도체/SYNTHETIC0002_annual_2024_12",
        "file_format": "xml",
        "n_files": 1,
    },
    {
        # 같은 연도의 분기보고서. doc_subtype 필터가 걸러야 한다.
        "doc_id": "periodic_SYNTHETIC0003",
        "corp_code": "00000001",
        "corp_name": "테스트반도체(주)",
        "stock_code": "000001",
        "doc_group": "periodic",
        "doc_subtype": "quarter",
        "report_nm": "분기보고서 (2025.03)",
        "is_correction": False,
        "rcept_no": "SYNTHETIC0003",
        "rcept_dt": "20250515",
        "base_year": 2025,
        "base_month": 3,
        "file_path": "raw/periodic/테스트반도체/SYNTHETIC0003_quarter_2025_03",
        "file_format": "xml",
        "n_files": 1,
    },
    {
        # 정정공시. 기본 필터가 제외해야 한다.
        "doc_id": "periodic_SYNTHETIC0004",
        "corp_code": "00000001",
        "corp_name": "테스트반도체(주)",
        "stock_code": "000001",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "[기재정정]사업보고서 (2025.12)",
        "is_correction": True,
        "rcept_no": "SYNTHETIC0009",
        "rcept_dt": "20260401",
        "base_year": 2025,
        "base_month": 12,
        "file_path": "raw/periodic/테스트반도체/SYNTHETIC0009_annual_2025_12",
        "file_format": "xml",
        "n_files": 1,
    },
    {
        # 다른 기업. 기업 필터가 걸러야 한다.
        "doc_id": "periodic_SYNTHETIC0005",
        "corp_code": "00000002",
        "corp_name": "테스트전자",
        "stock_code": "000002",
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "report_nm": "사업보고서 (2025.12)",
        "is_correction": False,
        "rcept_no": "SYNTHETIC0005",
        "rcept_dt": "20260312",
        "base_year": 2025,
        "base_month": 12,
        "file_path": "raw/periodic/테스트전자/SYNTHETIC0005_annual_2025_12",
        "file_format": "xml",
        "n_files": 1,
    },
    {
        # 같은 기업의 거래소공시. doc_group 필터가 걸러야 한다.
        #
        # 실제 manifest의 거래소공시 레코드는 base_year/base_month가 비어 있고 공시일만
        # 가지고 있다. 회계연도로 거르면 전건 탈락하는 조건을 fixture에서도 재현한다.
        "doc_id": "exchange_SYNTHETIC0006",
        "corp_code": "00000001",
        "corp_name": "테스트반도체(주)",
        "stock_code": "000001",
        "doc_group": "exchange",
        "doc_subtype": "단일판매공급계약체결",
        "report_nm": "단일판매ㆍ공급계약체결",
        "is_correction": False,
        "rcept_no": "SYNTHETIC0006",
        "rcept_dt": "20250601",
        "base_year": None,
        "base_month": None,
        "file_path": "raw/exchange/테스트반도체/SYNTHETIC0006",
        "file_format": "xml",
        "n_files": 1,
    },
    *[
        {
            # 같은 날 같은 유형의 원공시 2건.
            # 실제 코퍼스에서 (기업, 공시일, 유형)이 같은 문서가 여러 건인 경우가 있다.
            # 후보를 1건으로 좁히려면 본문 대조가 필요하며 메타데이터만으로는 못 고른다.
            "doc_id": f"exchange_{receipt_no}",
            "corp_code": "00000001",
            "corp_name": "테스트반도체(주)",
            "stock_code": "000001",
            "doc_group": "exchange",
            "doc_subtype": "단일판매공급계약체결",
            "report_nm": report_name,
            "is_correction": is_correction,
            "rcept_no": receipt_no,
            "rcept_dt": receipt_date,
            "base_year": None,
            "base_month": None,
            "file_path": f"raw/exchange/테스트반도체/{receipt_no}",
            "file_format": "xml",
            "n_files": 1,
        }
        for receipt_no, receipt_date, is_correction, report_name in (
            ("SYNTHETIC0011", "20250830", False, "단일판매ㆍ공급계약체결"),
            ("SYNTHETIC0012", "20250830", False, "단일판매ㆍ공급계약체결"),
            # 위 원공시를 정정하는 문서 2건. Pattern E에서는 이들이 대상 자체다.
            ("SYNTHETIC0013", "20251008", True, "[기재정정]단일판매ㆍ공급계약체결"),
            ("SYNTHETIC0014", "20251218", True, "[기재정정]단일판매ㆍ공급계약체결"),
        )
    ],
    {
        # 다른 세부유형이자 다음 연도. 유형·연도 필터가 걸러야 한다.
        "doc_id": "exchange_SYNTHETIC0015",
        "corp_code": "00000001",
        "corp_name": "테스트반도체(주)",
        "stock_code": "000001",
        "doc_group": "exchange",
        "doc_subtype": "신규시설투자등",
        "report_nm": "신규시설투자등",
        "is_correction": False,
        "rcept_no": "SYNTHETIC0015",
        "rcept_dt": "20260115",
        "base_year": None,
        "base_month": None,
        "file_path": "raw/exchange/테스트반도체/SYNTHETIC0015",
        "file_format": "xml",
        "n_files": 1,
    },
]


@pytest.fixture
def synthetic_document() -> dict[str, Any]:
    """Parser가 D-2/D-3 반영 후 내놓을 형태를 전제한 합성 parsed document."""
    with SYNTHETIC_DOCUMENT_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture
def ambiguous_document() -> dict[str, Any]:
    """
    본표와 주석표에 같은 지표가 동시에 존재하는 합성 parsed document.

    두 후보의 ACONTEXT, axis_depth, row_index가 모두 같아서
    표 분류 없이는 구분할 수 없다.
    """
    with SYNTHETIC_AMBIGUOUS_DOCUMENT_PATH.open("r", encoding="utf-8") as file:
        return json.load(file)


@pytest.fixture
def manifest_records() -> list[dict[str, Any]]:
    return copy.deepcopy(SYNTHETIC_MANIFEST_RECORDS)


@pytest.fixture
def synthetic_manifest_path(tmp_path: Path, manifest_records: list[dict[str, Any]]) -> Path:
    """합성 manifest.jsonl 파일을 만든다."""
    path = tmp_path / "manifest.jsonl"

    with path.open("w", encoding="utf-8") as file:
        for record in manifest_records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")

    return path


@pytest.fixture
def nfd_corpus_root(tmp_path: Path) -> Path:
    """
    한글 디렉터리명이 NFD(자모 분리)로 저장된 corpus를 만든다.

    manifest는 NFC 표기를 담고 있으므로 경로 해석기가 정규화 차이를 넘어설 수 있는지
    검증하기 위한 fixture다.
    """
    root = tmp_path / "corpus"

    # manifest에 있는 문서는 corpus에도 있어야 한다. 목록을 따로 관리하면 레코드를 추가할
    # 때마다 어긋나고, 그 어긋남이 '경로를 못 찾음'으로 나타나 진짜 실패와 구분되지 않는다.
    for record in SYNTHETIC_MANIFEST_RECORDS:
        directory = root / unicodedata.normalize("NFD", record["file_path"])

        directory.mkdir(parents=True, exist_ok=True)

        receipt_no = record["rcept_no"]

        # 거래소공시는 확장자가 xml이어도 실질적으로 HTML 구조다.
        body = "<html/>" if record["doc_group"] == "exchange" else "<DOCUMENT/>"

        (directory / f"{receipt_no}.xml").write_text(body, encoding="utf-8")

    # 본문과 첨부를 구분하는 규칙을 검증하기 위해 첨부 1건을 둔다.
    attachment_directory = root / unicodedata.normalize(
        "NFD", "raw/periodic/테스트반도체/SYNTHETIC0001_annual_2025_12"
    )

    (attachment_directory / "SYNTHETIC0001_00001.xml").write_text(
        "<DOCUMENT/>", encoding="utf-8"
    )

    return root


# --------------------------------------------------------------------------
# 거래소공시 체인 합성 fixture
#
# linker/diff는 manifest도 corpus도 읽지 않고 후보와 파싱 결과만 받는다. 그래서 실제 파일
# 없이 형태만 맞춘 객체로 규칙 전부를 검증할 수 있다.
# --------------------------------------------------------------------------

EVENT_SUBTYPE_SUPPLY_CONTRACT = "단일판매공급계약체결"


@dataclass(frozen=True)
class EventFilingStub:
    """
    linker가 요구하는 최소 공시 식별 정보.

    retrieval.metadata_filter.FilingRef와 같은 속성을 가진다. linker가 구조적 타입만
    요구하므로 실제 경로 해석 없이 규칙을 시험할 수 있다.
    """

    receipt_no: str
    receipt_date: str | None
    corp_name: str = "테스트반도체(주)"
    document_subtype: str = EVENT_SUBTYPE_SUPPLY_CONTRACT
    is_correction: bool = False


@pytest.fixture
def make_event_filing():
    """공시 1건을 만든다."""

    def _make(
        receipt_no: str,
        receipt_date: str | None,
        *,
        corp_name: str = "테스트반도체(주)",
        document_subtype: str = EVENT_SUBTYPE_SUPPLY_CONTRACT,
        is_correction: bool = False,
    ) -> EventFilingStub:
        return EventFilingStub(
            receipt_no=receipt_no,
            receipt_date=receipt_date,
            corp_name=corp_name,
            document_subtype=document_subtype,
            is_correction=is_correction,
        )

    return _make


@pytest.fixture
def make_exchange_document():
    """
    event.parser가 내놓는 형태의 문서를 만든다.

    changes는 (원문 필드명, 정정 전, 정정 후) 순서의 튜플 목록으로 준다.
    """

    def _make(
        *,
        original_filing_date: str | None = None,
        correction_date: str | None = None,
        changes: list[tuple[str, str, str]] | None = None,
        reason: str = "계약 조건 변경",
        is_correction: bool | None = None,
        title: str = "단일판매ㆍ공급계약 체결",
    ) -> dict[str, Any]:
        correcting = is_correction if is_correction is not None else correction_date is not None

        correction_block = None

        if correcting:
            correction_block = {
                "correction_date": correction_date,
                "original_filing_date": original_filing_date,
                "reason": reason,
                "changes": [
                    {
                        "raw_field": raw_field,
                        "field": raw_field,
                        "before": before,
                        "after": after,
                    }
                    for raw_field, before, after in (changes or [])
                ],
            }

        return {
            "metadata": {
                "doc_id": "exchange_SYNTHETIC",
                "document_type": "exchange",
                "is_correction": bool(correcting),
            },
            "title": title,
            "rows": [],
            "correction": correction_block,
        }

    return _make
