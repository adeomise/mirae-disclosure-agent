"""검색 메타데이터 필터 모듈.

역할:
- 기업, 보고서 유형, 기간, 이벤트 유형 조건으로 검색 후보 범위를 제한한다.

현재 구현 범위:
- corpus manifest(jsonl)를 읽어 기업/기간/문서군/정정 여부로 문서를 거른다.
- manifest의 file_path를 실제 파일시스템 경로로 해석한다.
- 정기공시는 1건 확정 조회를, 거래소공시는 후보 목록 조회를 제공한다.

의존 방향에 대한 메모:
- 이 모듈은 agent를 import하지 않는다. QueryPlan을 받지 않고 primitive 인자만 받는다.
- 이전에는 locate_filing이 QueryPlan을 직접 받아 retrieval -> agent 방향의 역전이 있었다.
  질문 해석 결과를 검색 조건으로 옮기는 일은 agent handler의 책임이다.
- 기업명 정규화(C1 Company Resolution)도 여기서 정의한다. 질의 해석 계층이 이 함수를
  가져다 쓴다.

어휘 대응에 대한 메모:
- Agent 설계 어휘(`document_class`)와 manifest 표기(`doc_group`)가 다르다.
  거래소공시는 설계에서 `event`, manifest에서 `exchange`다. 대응표를 이 모듈에 두고
  호출자는 설계 어휘만 쓴다.
- 기간의 의미도 문서군마다 다르다. 정기공시는 회계기간(`base_year`)으로 지정되지만
  거래소·지분공시는 manifest의 `base_year`가 비어 있고 공시일(`rcept_dt`)로만 지정된다.
  이 차이를 모르고 회계연도로 거르면 거래소공시가 조용히 전건 탈락한다.

경로 해석에 대한 메모(D-1):
- manifest의 한글 경로와 파일시스템에 저장된 한글 경로의 유니코드 정규화 형태가 다를 수 있다.
- 특정 정규화 형태(NFC/NFD)를 전역 정책으로 고정하지 않는다. 대신 후보 표기를 차례로
  시도하고 실제 존재하는 경로만 채택한다. 그래도 못 찾으면 디렉터리를 열거해 정규화 후
  비교한다.

입력:
- manifest.jsonl 경로, corpus 루트, 검색 조건 primitive

출력:
- FilingRef 1건 또는 FilingRef 목록

TODO:
- 지분공시 필터 조건 확인
- 정정공시 체인 연결(C7)은 event/linker의 책임이며 이 모듈은 후보만 제공한다
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Any

from disclosure_agent.common.dates import normalize_filing_date

# Agent 설계 어휘. docs/agent_design/query_plan.md의 `document_class` 값을 따른다.
DOCUMENT_CLASS_PERIODIC = "periodic"
DOCUMENT_CLASS_EVENT = "event"
DOCUMENT_CLASS_HOLDING = "holding"

# 설계 어휘 -> manifest 표기.
# 거래소공시만 이름이 다르다. 이 한 줄이 없으면 Pattern E가 문서를 한 건도 찾지 못한다.
DOC_GROUP_BY_DOCUMENT_CLASS: dict[str, str] = {
    DOCUMENT_CLASS_PERIODIC: "periodic",
    DOCUMENT_CLASS_EVENT: "exchange",
    DOCUMENT_CLASS_HOLDING: "holding",
}

DOCUMENT_CLASS_BY_DOC_GROUP: dict[str, str] = {
    doc_group: document_class
    for document_class, doc_group in DOC_GROUP_BY_DOCUMENT_CLASS.items()
}

# manifest의 정기공시 세부유형 표기 중 사업보고서.
#
# agent 계층의 DOCUMENT_SUBTYPE_ANNUAL과 값이 같지만 가져다 쓰지 않는다. retrieval은 agent를
# import하지 않으며(tests/unit/test_layering.py), 이 값의 출처는 설계 어휘가 아니라 manifest의
# doc_subtype 표기다.
DOC_SUBTYPE_ANNUAL = "annual"

# 기간 조건을 manifest의 어느 필드로 판정할지.
PERIOD_KIND_FISCAL = "fiscal"  # 회계기간. base_year
PERIOD_KIND_FILING = "filing"  # 공시일. rcept_dt

# 문서군별 기본 기간 해석.
# 거래소·지분공시는 manifest의 base_year가 비어 있으므로 회계연도로 거르면 전건 탈락한다.
# 호출자가 지정하지 않으면 문서군에 맞는 쪽을 쓴다.
DEFAULT_PERIOD_KIND_BY_DOCUMENT_CLASS: dict[str, str] = {
    DOCUMENT_CLASS_PERIODIC: PERIOD_KIND_FISCAL,
    DOCUMENT_CLASS_EVENT: PERIOD_KIND_FILING,
    DOCUMENT_CLASS_HOLDING: PERIOD_KIND_FILING,
}

# 법인격 표기는 회사명 비교 시 제거한다.
LEGAL_SUFFIX_PATTERN = re.compile(
    r"\((?:주|유|재|사|합)\)|주식회사|유한회사|유한책임회사|합자회사|합명회사"
)

NON_NAME_PATTERN = re.compile(r"[\s ·ㆍ・,\.\-_/]+")


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
    doc_group: str
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


def normalize_company_name(name: str) -> str:
    """
    회사명을 비교 가능한 형태로 정규화한다.

    - 유니코드 정규화(NFC)로 자모 분리/결합 표기를 통일한다.
    - '(주)', '주식회사' 같은 법인격 표기를 제거한다.
    - 공백과 구분 기호를 제거하고 소문자로 맞춘다.

    예:
        "한미반도체(주)"   -> "한미반도체"
        "한미반도체 주식회사" -> "한미반도체"
    """
    if not name:
        return ""

    text = unicodedata.normalize("NFC", name)

    text = LEGAL_SUFFIX_PATTERN.sub("", text)

    text = NON_NAME_PATTERN.sub("", text)

    return text.casefold()


def normalize_receipt_date(value: Any) -> str | None:
    """
    manifest의 공시일 표기를 YYYYMMDD로 맞춘다.

    규칙 자체는 common.dates가 가지고 있다. 거래소공시 본문의 날짜와 비교해야 하므로
    event 계층과 같은 규칙을 써야 한다. 기존 import 경로를 유지하려고 이름만 남긴다.
    """
    return normalize_filing_date(value)


def resolve_doc_group(document_class: str) -> str:
    """
    Agent 설계 어휘를 manifest 표기로 바꾼다.

    모르는 값은 조용히 통과시키지 않는다. 통과시키면 조건에 맞는 문서가 없는 것과
    어휘가 틀린 것이 같은 빈 결과로 보인다.
    """
    doc_group = DOC_GROUP_BY_DOCUMENT_CLASS.get(document_class)

    if doc_group is None:
        supported = ", ".join(sorted(DOC_GROUP_BY_DOCUMENT_CLASS))

        raise FilingNotFoundError(
            reason="unknown_document_class",
            detail=(
                f"알 수 없는 document_class입니다: '{document_class}'. "
                f"지원하는 값: {supported}"
            ),
        )

    return doc_group


def resolve_period_kind(document_class: str, period_kind: str | None = None) -> str:
    """
    기간 조건을 어느 필드로 판정할지 정한다.

    지정이 없으면 문서군의 기본값을 쓴다.
    """
    if period_kind is not None:
        if period_kind not in (PERIOD_KIND_FISCAL, PERIOD_KIND_FILING):
            raise FilingNotFoundError(
                reason="unknown_period_kind",
                detail=(
                    f"알 수 없는 period_kind입니다: '{period_kind}'. "
                    f"지원하는 값: {PERIOD_KIND_FISCAL}, {PERIOD_KIND_FILING}"
                ),
            )

        return period_kind

    return DEFAULT_PERIOD_KIND_BY_DOCUMENT_CLASS.get(document_class, PERIOD_KIND_FISCAL)


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


def list_company_universe(records: list[dict[str, Any]]) -> tuple[dict[str, str | None], ...]:
    """
    manifest에 등장하는 기업을 canonical 표기와 상장명 쌍으로 돌려준다.

    list_company_names와 나란히 두고 합치지 않는다. 돌려주는 것의 성질이 다르다.

        list_company_names      canonical `corp_name`만. 이 값이 downstream identity다.
        list_company_universe   같은 기업의 인식 후보 표기까지. 질문에서 알아보는 데만 쓴다.

    `listed_name`을 함께 넘기는 이유는 별도 하드코딩을 줄이기 위해서다. '현대차'·'삼성화재'
    ·'KT'는 manifest가 이미 알고 있는 표기이며, 그것을 코드에 다시 적으면 코퍼스가 바뀔 때
    두 벌이 어긋난다.

    같은 기업에 서로 다른 상장명이 여러 개면 하나를 고르지 않고 상장명을 비운다. 고르면
    고른 쪽이 조용히 인식 표기가 되고, 어느 표기로 인식했는지가 결과에 남지 않는다.
    """
    listed_names: dict[str, set[str]] = {}

    for record in records:
        name = record.get("corp_name")

        if not name:
            continue

        corp_name = unicodedata.normalize("NFC", str(name))

        seen = listed_names.setdefault(corp_name, set())

        listed = record.get("listed_name")

        if listed:
            seen.add(unicodedata.normalize("NFC", str(listed)))

    # 먼저 전부 모으고 나서 판정한다. 레코드를 훑으면서 정하면, 엇갈림을 확인해 비워 둔
    # 상장명이 뒤이어 나온 레코드 때문에 다시 채워진다.
    return tuple(
        {
            "corp_name": corp_name,
            "listed_name": next(iter(seen)) if len(seen) == 1 else None,
        }
        for corp_name, seen in listed_names.items()
    )


def latest_annual_fiscal_year_by_company(
    records: list[dict[str, Any]],
) -> dict[str, int]:
    """
    기업별로 코퍼스에 사업보고서가 존재하는 가장 최신 회계연도를 돌려준다.

    키는 normalize_company_name을 거친 표기다. 질의 해석 계층이 질문에서 읽은 기업명으로
    바로 찾을 수 있어야 하고, 그쪽도 같은 정규화 함수를 쓴다.

    이 값의 쓰임은 상대 기간 해석의 기준 연도(anchor)다
    (docs/agent_design/open_decisions.md D8). "작년"을 절대연도로 옮기려면 기준이 있어야 하고,
    그 기준을 시스템 시각에서 가져오면 같은 질문이 실행한 날에 따라 다른 해를 가리킨다.

    무엇을 보고 정하는가:
        정기공시(periodic) · 사업보고서(annual) · base_year 하나뿐이다.

        분기·반기보고서를 함께 보지 않는다. 이 코퍼스에는 70개사 전부 FY2026 분기보고서가
        있고 사업보고서는 FY2025가 최신이다. 문서군을 좁히지 않고 base_year의 최댓값을 취하면
        anchor가 2026이 되어, 존재하지 않는 FY2026 사업보고서를 찾다가 실패한다.

        요청 지표가 그 문서에서 실제로 읽히는지는 보지 않는다. 그것을 보기 시작하면 추출에
        실패한 해가 조용히 건너뛰어지고, "2025년 매출을 못 읽었으니 2024년을 최신으로 치자"가
        된다. 그 결과는 근거를 갖춘 채로 질문과 다른 기간을 답하는 형태이며, 추출 실패보다
        나쁘다. anchor는 문서의 존재만으로 정하고, 값이 없으면 뒤 단계가 NOT_FOUND로 답한다.

    정정공시를 제외하지 않는 이유:
        [기재정정]사업보고서도 그 회계연도의 사업보고서다. 여기서 묻는 것은 "그 해의 사업보고서가
        코퍼스에 있는가"이지 "어느 판본을 읽을 것인가"가 아니다. 판본 선택은 D5이며 이 함수가
        정하지 않는다.

        제외하면 실제로 갈린다. 이 코퍼스에서 한화솔루션의 FY2025 사업보고서는 정정본 한 건만
        있으므로, 정정본을 빼면 그 기업만 anchor가 2024가 되고 나머지 69개사는 2025가 된다.
        같은 질문이 기업에 따라 다른 해를 가리키는 것은 정책이 아니라 사고다.

    base_year가 정수가 아닌 레코드는 건너뛴다. 문자열을 정수로 바꿔 받아들이지 않는 이유는,
    표기가 흔들리는 manifest에서 anchor를 만들어 내는 것보다 anchor 없이 막히는 쪽이 낫기
    때문이다. 값이 없으면 호출자가 fail-closed로 처리한다.
    """
    doc_group = DOC_GROUP_BY_DOCUMENT_CLASS[DOCUMENT_CLASS_PERIODIC]

    anchors: dict[str, int] = {}

    for record in records:
        if record.get("doc_group") != doc_group:
            continue

        if record.get("doc_subtype") != DOC_SUBTYPE_ANNUAL:
            continue

        base_year = record.get("base_year")

        # bool은 int의 하위 타입이라 isinstance(True, int)가 참이다. 회계연도 자리에 True가
        # 들어오면 1이 되어 조용히 섞인다.
        if not isinstance(base_year, int) or isinstance(base_year, bool):
            continue

        name = normalize_company_name(record.get("corp_name", ""))

        if not name:
            continue

        if base_year > anchors.get(name, base_year - 1):
            anchors[name] = base_year

    return anchors


def latest_annual_fiscal_year(
    records: list[dict[str, Any]],
    *,
    company: str,
) -> int | None:
    """
    기업 한 곳의 상대 기간 기준 연도(anchor)를 돌려준다. 없으면 None이며 추측하지 않는다.

    판정 규칙은 latest_annual_fiscal_year_by_company 하나가 갖는다. 여기서 같은 조건을 다시
    적으면 두 벌이 되고, 나중에 한쪽만 넓어지는 날이 온다.
    """
    return latest_annual_fiscal_year_by_company(records).get(
        normalize_company_name(company)
    )


def _matches_period(
    record: dict[str, Any],
    *,
    year: int | None,
    period_kind: str,
    date_range: tuple[str, str] | None,
) -> bool:
    """
    기간 조건을 판정한다.

    year의 의미는 period_kind가 정한다. date_range는 종류와 무관하게 공시일에 적용한다.
    """
    receipt_date = normalize_receipt_date(record.get("rcept_dt"))

    if year is not None:
        if period_kind == PERIOD_KIND_FISCAL:
            if record.get("base_year") != year:
                return False

        else:
            if receipt_date is None or int(receipt_date[:4]) != year:
                return False

    if date_range is not None:
        start = normalize_receipt_date(date_range[0])
        end = normalize_receipt_date(date_range[1])

        if start is None or end is None:
            raise FilingNotFoundError(
                reason="unparsable_date_range",
                detail=f"해석할 수 없는 공시일 범위입니다: {date_range!r}",
            )

        if receipt_date is None or not (start <= receipt_date <= end):
            return False

    return True


def filter_filings(
    records: list[dict[str, Any]],
    *,
    company: str,
    document_class: str,
    document_subtype: str | None = None,
    year: int | None = None,
    period_kind: str | None = None,
    date_range: tuple[str, str] | None = None,
    include_corrections: bool = False,
) -> list[dict[str, Any]]:
    """
    manifest 레코드를 검색 조건으로 거른다.

    - 기업명은 '(주)' 등 법인격 표기를 제거한 정규화 형태로 비교한다.
    - document_subtype이 None이면 세부유형을 거르지 않는다. 거래소공시처럼 질문이 유형을
      지정하지 않는 경우가 있다.
    - year의 의미는 period_kind가 정하며, 지정하지 않으면 문서군 기본값을 쓴다.
    - include_corrections는 호출자가 명시한다. Pattern E는 정정본이 대상 자체이므로
      제외하면 안 된다.

    manifest 순서를 유지한다. 정렬과 선택은 호출자가 정한다.
    """
    doc_group = resolve_doc_group(document_class)

    resolved_period_kind = resolve_period_kind(document_class, period_kind)

    target = normalize_company_name(company)

    matched: list[dict[str, Any]] = []

    for record in records:
        if normalize_company_name(record.get("corp_name", "")) != target:
            continue

        if record.get("doc_group") != doc_group:
            continue

        if document_subtype is not None and record.get("doc_subtype") != document_subtype:
            continue

        if not _matches_period(
            record,
            year=year,
            period_kind=resolved_period_kind,
            date_range=date_range,
        ):
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


def sort_filings(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    공시일, 접수번호 순으로 정렬한다.

    manifest 순서에 의존하지 않도록 후보 목록을 돌려주기 전에 쓴다. 정렬은 순서를 고정할
    뿐이고 어느 것이 정답인지는 정하지 않는다.
    """
    return sorted(
        records,
        key=lambda record: (
            normalize_receipt_date(record.get("rcept_dt")) or "",
            str(record.get("rcept_no", "")),
        ),
    )


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

    DART 공시는 '<접수번호>.xml'이 본문이고 '<접수번호>_<일련번호>.xml'은 첨부다.
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


def resolve_filing_ref(record: dict[str, Any], *, corpus_root: str | Path) -> FilingRef:
    """
    manifest 레코드 1건을 실제 파일 경로까지 해석해 FilingRef로 만든다.

    경로를 해석하지 못하면 조용히 건너뛰지 않고 오류를 올린다. 체인을 다루는 Pattern E에서
    문서 한 건이 말없이 빠지면 이력이 완결된 것처럼 보이기 때문이다.
    """
    receipt_no = str(record.get("rcept_no", ""))

    directory = resolve_existing_path(corpus_root, record.get("file_path", ""))

    if directory is None:
        raise FilingNotFoundError(
            reason="path_unresolved",
            detail=(
                f"manifest의 file_path를 실제 경로로 해석하지 못했습니다: "
                f"{record.get('file_path')!r} (rcept_no={receipt_no})"
            ),
        )

    main_document = find_main_document(directory, receipt_no)

    if main_document is None:
        raise FilingNotFoundError(
            reason="main_document_missing",
            detail=f"공시 디렉터리에서 본문 XML을 찾지 못했습니다: {directory}",
        )

    doc_group = str(record.get("doc_group", ""))

    return FilingRef(
        doc_id=record.get("doc_id", f"{doc_group}_{receipt_no}"),
        corp_name=unicodedata.normalize("NFC", record.get("corp_name", "")),
        corp_code=record.get("corp_code"),
        stock_code=record.get("stock_code"),
        report_name=record.get("report_nm", ""),
        receipt_no=receipt_no,
        receipt_date=record.get("rcept_dt"),
        document_class=DOCUMENT_CLASS_BY_DOC_GROUP.get(doc_group, doc_group),
        doc_group=doc_group,
        document_subtype=record.get("doc_subtype") or "",
        base_year=record.get("base_year"),
        base_month=record.get("base_month"),
        is_correction=bool(record.get("is_correction")),
        directory=directory,
        main_document=main_document,
    )


def locate_filing(
    *,
    manifest_path: str | Path,
    corpus_root: str | Path,
    company: str,
    document_class: str,
    document_subtype: str | None = None,
    year: int | None = None,
    period_kind: str | None = None,
    date_range: tuple[str, str] | None = None,
    include_corrections: bool = False,
) -> FilingRef:
    """
    조건에 맞는 공시 1건을 찾아 실제 파일 경로까지 해석한다.

    후보가 여럿이면 접수번호가 가장 큰 것을 고른다. 문서 1건이 답을 결정하는 정기공시
    조회를 위한 함수이며, 후보 자체가 답인 거래소공시에는 list_filings를 쓴다.
    """
    records = load_manifest(manifest_path)

    matched = filter_filings(
        records,
        company=company,
        document_class=document_class,
        document_subtype=document_subtype,
        year=year,
        period_kind=period_kind,
        date_range=date_range,
        include_corrections=include_corrections,
    )

    selected = select_latest_filing(matched)

    if selected is None:
        raise FilingNotFoundError(
            reason="no_matching_filing",
            detail=(
                f"manifest에서 조건에 맞는 공시를 찾지 못했습니다: "
                f"{company} / {year} / {document_class} / {document_subtype}"
            ),
        )

    return resolve_filing_ref(selected, corpus_root=corpus_root)


def list_filings(
    *,
    manifest_path: str | Path,
    corpus_root: str | Path,
    company: str,
    document_class: str,
    document_subtype: str | None = None,
    year: int | None = None,
    period_kind: str | None = None,
    date_range: tuple[str, str] | None = None,
    include_corrections: bool = False,
) -> list[FilingRef]:
    """
    조건에 맞는 공시 후보 전부를 돌려준다.

    1건으로 좁히지 않는다. 같은 기업이 같은 날 같은 유형의 공시를 여러 건 제출하는 경우가
    실제로 있고, 그때 문서 순서로 하나를 고르면 근거 없이 다른 계약을 답하게 된다.
    어느 것을 쓸지는 본문 대조(C3 Document Disambiguation)로 정할 일이며 여기서 정하지 않는다.

    공시일과 접수번호 순으로 정렬해 돌려준다. 정렬은 순서를 고정할 뿐이고 우선순위가 아니다.
    """
    records = load_manifest(manifest_path)

    matched = filter_filings(
        records,
        company=company,
        document_class=document_class,
        document_subtype=document_subtype,
        year=year,
        period_kind=period_kind,
        date_range=date_range,
        include_corrections=include_corrections,
    )

    return [
        resolve_filing_ref(record, corpus_root=corpus_root)
        for record in sort_filings(matched)
    ]


def list_event_filings(
    *,
    manifest_path: str | Path,
    corpus_root: str | Path,
    company: str,
    include_corrections: bool,
    document_subtype: str | None = None,
    year: int | None = None,
    date_range: tuple[str, str] | None = None,
) -> list[FilingRef]:
    """
    거래소공시 후보를 조회한다.

    include_corrections에 기본값을 두지 않은 것은 의도적이다. Pattern E는 정정본이 대상
    자체이므로 제외하면 이력이 통째로 사라지고, Pattern B의 단건 요약은 판본 정책에 따라
    달라진다. 어느 쪽인지 호출자가 밝히게 한다.

    기간은 공시일 기준으로 판정한다. manifest의 거래소공시 레코드에는 base_year가 비어 있다.
    """
    return list_filings(
        manifest_path=manifest_path,
        corpus_root=corpus_root,
        company=company,
        document_class=DOCUMENT_CLASS_EVENT,
        document_subtype=document_subtype,
        year=year,
        period_kind=PERIOD_KIND_FILING,
        date_range=date_range,
        include_corrections=include_corrections,
    )
