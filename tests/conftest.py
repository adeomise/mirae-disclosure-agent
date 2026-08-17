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
        "doc_id": "exchange_SYNTHETIC0006",
        "corp_code": "00000001",
        "corp_name": "테스트반도체(주)",
        "stock_code": "000001",
        "doc_group": "exchange",
        "doc_subtype": "supply_contract",
        "report_nm": "단일판매ㆍ공급계약체결",
        "is_correction": False,
        "rcept_no": "SYNTHETIC0006",
        "rcept_dt": "20250601",
        "base_year": 2025,
        "base_month": 6,
        "file_path": "raw/exchange/테스트반도체/SYNTHETIC0006",
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

    relative = "raw/periodic/테스트반도체/SYNTHETIC0001_annual_2025_12"

    directory = root / unicodedata.normalize("NFD", relative)

    directory.mkdir(parents=True, exist_ok=True)

    (directory / "SYNTHETIC0001.xml").write_text("<DOCUMENT/>", encoding="utf-8")

    (directory / "SYNTHETIC0001_00001.xml").write_text("<DOCUMENT/>", encoding="utf-8")

    return root
