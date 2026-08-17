"""검색 메타데이터 필터 모듈.

역할:
- 기업, 보고서 유형, 기간, 이벤트 유형 조건으로 검색 후보 범위를 제한한다.

현재 구현 범위:
- corpus manifest(jsonl)를 읽어 정기공시 문서를 기업/연도/유형으로 필터링한다.
- manifest의 file_path를 실제 파일시스템 경로로 해석한다.

경로 해석에 대한 메모(D-1):
- manifest의 한글 경로와 파일시스템에 저장된 한글 경로의 유니코드 정규화 형태가 다를 수 있다.
- 특정 정규화 형태(NFC/NFD)를 전역 정책으로 고정하지 않는다. 대신 후보 표기를 차례로
  시도하고 실제 존재하는 경로만 채택한다. 그래도 못 찾으면 디렉터리를 열거해 정규화 후
  비교한다.

입력:
- manifest.jsonl 경로, corpus 루트, QueryPlan

출력:
- FilingRef (선택된 공시 1건과 실제 파일 경로)

TODO:
- 이벤트·지분공시 필터 조건 추가
- 정정공시 연결 규칙 반영
"""

from __future__ import annotations

import json
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from disclosure_agent.agent.query_analyzer import QueryPlan, normalize_company_name


class FilingNotFoundError(LookupError):
    """조건에 맞는 공시를 manifest에서 찾지 못했거나 실제 파일이 없는 경우."""

    def __init__(self, reason: str, detail: str) -> None:
        super().__init__(detail)

        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class FilingRef:
    """선택된 공시 1건과 해석된 로컬 경로."""

    doc_id: str
    corp_name: str
    corp_code: str | None
    stock_code: str | None
    report_name: str
    receipt_no: str
    receipt_date: str | None
    document_class: str
    document_subtype: str
    base_year: int | None
    base_month: int | None
    is_correction: bool
    directory: Path
    main_document: Path

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)

        data["directory"] = str(self.directory)
        data["main_document"] = str(self.main_document)

        return data


def load_manifest(manifest_path: str | Path) -> list[dict[str, Any]]:
    """
    manifest.jsonl을 읽어 레코드 목록을 돌려준다.

    빈 줄은 건너뛴다.
    """
    manifest_path = Path(manifest_path)

    if not manifest_path.exists():
        raise FilingNotFoundError(
            reason="manifest_missing",
            detail=f"manifest를 찾을 수 없습니다: {manifest_path}",
        )

    records: list[dict[str, Any]] = []

    with manifest_path.open("r", encoding="utf-8") as file:
        for line in file:
            line = line.strip()

            if not line:
                continue

            records.append(json.loads(line))

    return records


def list_company_names(records: list[dict[str, Any]]) -> tuple[str, ...]:
    """
    manifest에 등장하는 기업명을 중복 없이 돌려준다.

    query_analyzer가 기업명을 추측하지 않도록 인식 후보로 넘겨준다.
    """
    names: dict[str, None] = {}

    for record in records:
        name = record.get("corp_name")

        if name:
            names.setdefault(unicodedata.normalize("NFC", name), None)

    return tuple(names)


def filter_filings(
    records: list[dict[str, Any]],
    *,
    company: str,
    year: int,
    document_class: str,
    document_subtype: str,
    include_corrections: bool = False,
) -> list[dict[str, Any]]:
    """
    기업/연도/문서 유형으로 manifest 레코드를 거른다.

    기업명은 '(주)' 등 법인격 표기를 제거한 정규화 형태로 비교한다.
    """
    target = normalize_company_name(company)

    matched: list[dict[str, Any]] = []

    for record in records:
        if normalize_company_name(record.get("corp_name", "")) != target:
            continue

        if record.get("doc_group") != document_class:
            continue

        if record.get("doc_subtype") != document_subtype:
            continue

        if record.get("base_year") != year:
            continue

        if not include_corrections and record.get("is_correction"):
            continue

        matched.append(record)

    return matched


def select_latest_filing(records: list[dict[str, Any]]) -> dict[str, Any] | None:
    """
    후보 중 접수번호가 가장 큰(가장 최근) 공시를 고른다.
    """
    if not records:
        return None

    return max(records, key=lambda record: str(record.get("rcept_no", "")))


def _normalization_candidates(text: str) -> list[str]:
    """
    같은 문자열의 유니코드 정규화 후보를 중복 없이 나열한다.

    원본 표기를 먼저 두어 이미 맞는 경우 추가 비용이 들지 않게 한다.
    """
    candidates: list[str] = []

    for form in (text, unicodedata.normalize("NFC", text), unicodedata.normalize("NFD", text)):
        if form not in candidates:
            candidates.append(form)

    return candidates


def _match_child(directory: Path, name: str) -> Path | None:
    """
    directory 안에서 name과 같은 항목을 찾는다.

    1) 정규화 후보를 그대로 붙여 존재하는지 본다.
    2) 실패하면 디렉터리를 열거해 NFC 기준으로 비교한다.
    """
    for candidate_name in _normalization_candidates(name):
        candidate = directory / candidate_name

        if candidate.exists():
            return candidate

    targets = {
        unicodedata.normalize("NFC", candidate_name)
        for candidate_name in _normalization_candidates(name)
    }

    try:
        children = list(directory.iterdir())

    except OSError:
        return None

    for child in children:
        if unicodedata.normalize("NFC", child.name) in targets:
            return child

    return None


def resolve_existing_path(root: str | Path, relative_path: str | Path) -> Path | None:
    """
    corpus 루트 기준 상대 경로를 실제 존재하는 경로로 해석한다.

    유니코드 정규화 형태를 고정하지 않고 존재 여부로 판정한다.
    찾지 못하면 None을 돌려준다.
    """
    root = Path(root)

    normalized = str(relative_path).replace("\\", "/")

    for candidate_text in _normalization_candidates(normalized):
        candidate = root / candidate_text

        if candidate.exists():
            return candidate

    if not root.exists():
        return None

    current = root

    for part in PurePosixPath(normalized).parts:
        if part in ("", "."):
            continue

        matched = _match_child(current, part)

        if matched is None:
            return None

        current = matched

    return current


def find_main_document(directory: Path, receipt_no: str) -> Path | None:
    """
    공시 디렉터리에서 본문 XML을 고른다.

    DART 정기공시는 '<접수번호>.xml'이 본문이고 '<접수번호>_<일련번호>.xml'은 첨부다.
    """
    primary = _match_child(directory, f"{receipt_no}.xml")

    if primary is not None and primary.is_file():
        return primary

    try:
        candidates = sorted(
            child
            for child in directory.iterdir()
            if child.is_file() and child.suffix.lower() == ".xml"
        )

    except OSError:
        return None

    if not candidates:
        return None

    return candidates[0]


def locate_filing(
    plan: QueryPlan,
    *,
    manifest_path: str | Path,
    corpus_root: str | Path,
) -> FilingRef:
    """
    QueryPlan 조건에 맞는 공시 1건을 찾아 실제 파일 경로까지 해석한다.
    """
    records = load_manifest(manifest_path)

    matched = filter_filings(
        records,
        company=plan.company,
        year=plan.year,
        document_class=plan.document_class,
        document_subtype=plan.document_subtype,
    )

    selected = select_latest_filing(matched)

    if selected is None:
        raise FilingNotFoundError(
            reason="no_matching_filing",
            detail=(
                f"manifest에서 조건에 맞는 공시를 찾지 못했습니다: "
                f"{plan.company} / {plan.year} / {plan.document_class} / {plan.document_subtype}"
            ),
        )

    directory = resolve_existing_path(corpus_root, selected["file_path"])

    if directory is None:
        raise FilingNotFoundError(
            reason="path_unresolved",
            detail=(
                f"manifest의 file_path를 실제 경로로 해석하지 못했습니다: "
                f"{selected['file_path']}"
            ),
        )

    receipt_no = str(selected.get("rcept_no", ""))

    main_document = find_main_document(directory, receipt_no)

    if main_document is None:
        raise FilingNotFoundError(
            reason="main_document_missing",
            detail=f"공시 디렉터리에서 본문 XML을 찾지 못했습니다: {directory}",
        )

    return FilingRef(
        doc_id=selected.get("doc_id", f"periodic_{receipt_no}"),
        corp_name=unicodedata.normalize("NFC", selected.get("corp_name", "")),
        corp_code=selected.get("corp_code"),
        stock_code=selected.get("stock_code"),
        report_name=selected.get("report_nm", ""),
        receipt_no=receipt_no,
        receipt_date=selected.get("rcept_dt"),
        document_class=selected.get("doc_group", plan.document_class),
        document_subtype=selected.get("doc_subtype", plan.document_subtype),
        base_year=selected.get("base_year"),
        base_month=selected.get("base_month"),
        is_correction=bool(selected.get("is_correction")),
        directory=directory,
        main_document=main_document,
    )
