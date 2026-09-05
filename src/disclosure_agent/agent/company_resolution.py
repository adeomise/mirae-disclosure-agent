"""기업명 해석(Company Resolution) 모듈.

역할:
- 질문에 적힌 기업 표현을 코퍼스의 canonical 기업(`corp_name`) 하나로 옮긴다.
- 옮길 수 없거나 후보가 둘 이상이면 고르지 않고 그 사실을 그대로 돌려준다.

이 경계를 query_analyzer 밖에 두는 이유:
    질의 해석은 '무엇을 물었는가'를 가르는 일이고, 기업명 해석은 '어느 회사인가'를 가르는
    일이다. 둘을 한 함수에 쌓으면 별칭 규칙이 늘 때마다 의도 분기 코드가 함께 커지고,
    별칭 규칙만 따로 시험할 수 없게 된다. 여기서는 질문 문자열과 기업 목록만 받고
    QueryPlan도 manifest도 모른다.

해석 우선순위(위에서부터 먼저 본다):
    1. canonical `corp_name` 그대로
    2. 정규화한 canonical `corp_name`      -- 1과 같은 tier다. '(주)'·공백 차이만 흡수한다.
    3. `listed_name`(상장명)
    4. 명시적으로 등록한 alias
    5. 부분명(partial) 후보 해석
    6. 후보 수로 판정: 1개면 resolve, 2개 이상이면 AMBIGUOUS_MATCH, 0개면 미인식

    1~4는 '표기가 정확히 일치'하는 갈래이고 tier로 구분한다. 같은 문자열이 두 tier에
    걸리면 위 순서가 이긴다. 같은 tier 안에서 두 기업에 걸리면 고르지 않고 AMBIGUOUS_MATCH다.

부분명 해석의 규약:
    후보를 좁힐 때 '가장 유명한 회사', '가장 짧은 이름', '검색 상위' 같은 기준을 쓰지 않는다.
    쓰는 순간 그 기준이 조용히 답을 고른 것이 되고, 고른 사실이 결과에 남지 않는다.
    후보가 둘 이상이면 언제나 막는다.

    후보를 세는 규칙과 실제로 옮기는 규칙을 나눠 둔다.

        입구(gate)  표현이 등록된 표기 중 하나의 '앞부분'이어야 부분명 후보 조회를 연다.
                    이 조건이 없으면 '금융'·'기술'·'전기'처럼 회사명 안에 우연히 들어가는
                    일반 낱말이 전부 기업 언급으로 읽혀, 답할 수 있던 질문이 모호성으로
                    막힌다. 반대로 이 조건만으로 후보를 세면 안 된다. 아래를 보라.

        후보 집계   gate를 통과하면 '포함(substring)' 기준으로 후보를 모은다. 앞부분 기준으로만
                    세면 후보가 줄어들고, 줄어든 결과로 unique가 되는 일이 생긴다. 그것은
                    막아야 할 것을 통과시키는 방향의 오류다. 후보 집계는 언제나 넓은 쪽이다.

        해석 하한   후보가 1개여도 표현이 PARTIAL_MIN_RESOLVE_LENGTH보다 짧으면 옮기지 않는다.
                    '하나'(하나금융지주)·'미래'(미래에셋증권)처럼 두 글자 일반 낱말이 회사
                    이름의 앞부분과 겹치는 경우가 있고, 그 근거로 기업을 확정하면 질문에 없던
                    회사가 답에 들어온다. 이 하한은 후보 집계에는 적용하지 않는다. 적용하면
                    '삼성'처럼 짧은 표현의 모호성이 사라져 한 회사로 확정된다.

alias 정책:
    편집거리·LLM 추론으로 비슷해 보이는 회사를 고르지 않는다. 등록한 표기만 옮긴다.
    등록 대상은 과거 사명, 시장에서 통용되는 표기, 팀이 명시적으로 승인한 축약명뿐이며,
    목록에 없는 축약은 부분명 규칙을 통과하지 못하는 한 인식되지 않는다.

    대상 기업이 지금 코퍼스에 없으면 그 alias는 등록하지 않는다. 코퍼스에 없는 회사를
    이름만으로 인식하면 뒤 단계가 '문서를 못 찾았다'로 답하게 되고, 실제로 일어난 일
    ('그 회사는 여기 없다')과 사유가 달라진다.

입력:
- 질문 문자열
- 기업 목록. `corp_name`만 있는 목록이거나, `listed_name`을 함께 담은 universe다.

출력:
- CompanyResolution. 질문에 나온 순서대로의 CompanyMention 목록이며, 각 mention은
  raw / normalized / canonical / method / candidates를 모두 남긴다.

여기서 예외를 던지지 않는 이유:
    '막는다'는 판정은 질의 해석 계층의 어휘(UnsupportedQuestionError)로 표현된다. 이 모듈이
    그 예외를 던지면 기업명 해석 규칙을 시험하는 자리마다 질의 계층 예외를 알아야 한다.
    여기서는 사실만 돌려주고, 옮길지 막을지는 부르는 쪽이 정한다.
"""

from __future__ import annotations

import bisect
import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass

from disclosure_agent.retrieval.metadata_filter import (
    LEGAL_SUFFIX_PATTERN,
    NON_NAME_PATTERN,
    normalize_company_name,
)

__all__ = [
    "COMPANY_ALIASES",
    "PARTIAL_MIN_CANDIDATE_LENGTH",
    "PARTIAL_MIN_RESOLVE_LENGTH",
    "RESOLUTION_ALIAS",
    "RESOLUTION_AMBIGUOUS_MATCH",
    "RESOLUTION_CANONICAL",
    "RESOLUTION_LISTED_NAME",
    "RESOLUTION_UNIQUE_PARTIAL",
    "CompanyEntry",
    "CompanyMention",
    "CompanyResolution",
    "CompanyResolver",
    "normalize_company_name_with_offsets",
]

# 해석 방법. 결과에 그대로 실려 provenance가 된다.
RESOLUTION_CANONICAL = "canonical"
RESOLUTION_LISTED_NAME = "listed_name"
RESOLUTION_ALIAS = "alias"
RESOLUTION_UNIQUE_PARTIAL = "unique_partial"

# 옮기지 못한 이유. 후보가 둘 이상일 때만 쓴다. 후보가 0개인 것은 '이 표현은 기업 언급이
# 아니다'이며 mention 자체를 만들지 않는다.
RESOLUTION_AMBIGUOUS_MATCH = "ambiguous_match"

# tier가 작을수록 먼저다. 같은 문자열이 두 갈래에 걸릴 때의 순서이며, 위 docstring의
# 해석 우선순위 1~4에 대응한다.
_TIER_CANONICAL = 0
_TIER_LISTED_NAME = 1
_TIER_ALIAS = 2

_METHOD_BY_TIER: Mapping[int, str] = {
    _TIER_CANONICAL: RESOLUTION_CANONICAL,
    _TIER_LISTED_NAME: RESOLUTION_LISTED_NAME,
    _TIER_ALIAS: RESOLUTION_ALIAS,
}

# 부분명 후보 조회를 여는 최소 길이. 정규화한 표기 기준이다.
#
# 두 글자를 후보 집계에서 빼지 않는 이유가 규약이다. '삼성'은 두 글자이고 후보가 여덟이며,
# 여기서 빼면 그 모호성이 보이지 않는 채로 질문이 통과한다.
PARTIAL_MIN_CANDIDATE_LENGTH = 2

# 부분명 후보가 1개일 때 실제로 옮기는 최소 길이.
#
# 후보 집계(PARTIAL_MIN_CANDIDATE_LENGTH)보다 길다. 이 차이가 '두 글자 표현은 모호성을
# 드러내는 데만 쓰고 단독으로 회사를 확정하지는 않는다'를 뜻한다.
PARTIAL_MIN_RESOLVE_LENGTH = 3

# 명시적으로 등록한 alias. 키는 질문에 적히는 표기, 값은 canonical `corp_name`이다.
#
# 여기에 없는 축약은 인식되지 않는다. 그것이 이 표를 두는 이유다. 비슷해 보이는 이름을
# 자동으로 잇는 규칙을 두면 어느 회사로 옮겨졌는지가 표가 아니라 알고리즘에 숨는다.
#
# 넣고 빼는 것은 팀의 판단이다. 코드가 스스로 늘리지 않는다.
COMPANY_ALIASES: Mapping[str, str] = {
    # 시장에서 널리 쓰이는 축약. 다른 상장사와 겹치지 않는 것만 넣는다.
    "삼바": "삼성바이오로직스",
    "하이닉스": "SK하이닉스",
    # 과거 사명·통용 표기. canonical이 짧아진 경우다.
    "기아자동차": "기아",
    # canonical이 로마자인 회사의 한글 표기.
    "네이버": "NAVER",
}

# 기업명 뒤에 붙는 조사. 부분명 후보를 만들 때만 뗀다.
#
# 긴 것부터 본다. '이랑'을 '이'로 떼면 '삼성이랑'이 '삼성랑'이 되어 아무 데도 걸리지 않는다.
#
# 조사를 떼는 자리를 부분명 후보 생성으로 한정한 이유:
#     표기가 정확히 일치하는 갈래(canonical/listed/alias)는 질문 문자열 안에서 그대로
#     찾으므로 조사를 뗄 필요가 없다. 반대로 부분명은 낱말 단위로 봐야 하고, 낱말에는
#     조사가 붙어 온다.
MENTION_PARTICLES: tuple[str, ...] = (
    "이랑",
    "에서",
    "에게",
    "한테",
    "부터",
    "까지",
    "보다",
    "처럼",
    "으로",
    "와의",
    "과의",
    "하고",
    "이나",
    "가",
    "이",
    "은",
    "는",
    "을",
    "를",
    "의",
    "과",
    "와",
    "도",
    "만",
    "랑",
    "에",
    "로",
    "나",
)

# 낱말 양끝에서 떼는 문장부호. 회사명에 쓰이는 '&'·'+'는 넣지 않는다.
_TOKEN_TRIM_CHARS = "?!.,·ㆍ、'\"“”‘’()[]{}<>《》「」:;~"

# 낱말 경계. 공백 종류를 가리지 않는다.
_TOKEN_PATTERN = re.compile(r"\S+")


def normalize_company_name_with_offsets(text: str) -> tuple[str, tuple[int, ...]]:
    """
    normalize_company_name과 같은 문자열을 만들되, 글자마다 원문 위치를 함께 남긴다.

    왜 위치가 필요한가:
        표기가 정확히 일치하는 갈래는 정규화한 질문 문자열 위에서 찾는다(공백·'(주)'가
        지워져 있어야 표기 차이를 흡수한다). 부분명은 반대로 낱말 경계를 봐야 하므로 원문
        위에서 잘라야 한다. 두 좌표계를 오갈 방법이 없으면, 이미 회사로 잡힌 자리를 부분명이
        다시 집는 것을 막을 수 없다.

    돌려주는 offsets[i]는 정규화 문자열의 i번째 글자가 온 NFC 원문의 위치다. 단조증가하므로
    이분 탐색으로 원문 구간 <-> 정규화 구간을 옮길 수 있다.

    normalize_company_name과 같은 결과를 내는지는 테스트가 고정한다
    (tests/unit/test_company_resolution.py). 정규화 규칙이 한쪽만 바뀌면 그 테스트가 깨진다.
    """
    if not text:
        return "", ()

    composed = unicodedata.normalize("NFC", text)

    # 지울 글자를 표시만 하고 실제로 지우지 않는다. 지우면서 위치를 세면 규칙이 하나 늘 때마다
    # 위치 계산이 함께 복잡해진다.
    #
    # 법인격 표기를 먼저 찾는 것이 normalize_company_name의 순서와 같다. 구분 기호는 글자
    # 단위 판정이라 순서에 영향받지 않는다.
    keep = [True] * len(composed)

    for pattern in (LEGAL_SUFFIX_PATTERN, NON_NAME_PATTERN):
        for match in pattern.finditer(composed):
            for index in range(*match.span()):
                keep[index] = False

    characters: list[str] = []
    offsets: list[int] = []

    for index, character in enumerate(composed):
        if not keep[index]:
            continue

        folded = character.casefold()

        characters.append(folded)

        # casefold가 글자를 늘리는 경우(예: 'ß' -> 'ss')에도 원문 위치는 하나다.
        offsets.extend([index] * len(folded))

    return "".join(characters), tuple(offsets)


def find_unclaimed_span(
    haystack: str,
    needle: str,
    claimed: Sequence[tuple[int, int]],
) -> tuple[int, int] | None:
    """
    이미 다른 회사명이 차지한 구간과 겹치지 않는 첫 출현 위치를 찾는다. 없으면 None.

    이 검사가 다중 기업 판정의 false positive를 막는 자리다. 단순 포함 검사만 하면
    'LG생활건강'을 물은 질문에서 'LG'가 또 매치되어 한 기업이 두 기업으로 세어진다.
    """
    if not needle:
        return None

    start = haystack.find(needle)

    while start != -1:
        end = start + len(needle)

        if not _overlaps(start, end, claimed):
            return start, end

        start = haystack.find(needle, start + 1)

    return None


def _overlaps(start: int, end: int, claimed: Sequence[tuple[int, int]]) -> bool:
    return any(
        claim_start < end and start < claim_end for claim_start, claim_end in claimed
    )


@dataclass(frozen=True)
class CompanyEntry:
    """
    인식 후보 기업 하나.

    corp_name이 canonical identity다. listed_name은 있으면 인식 후보 표기로만 쓰이며,
    downstream으로 나가지 않는다.
    """

    corp_name: str
    listed_name: str | None = None


@dataclass(frozen=True)
class CompanyMention:
    """
    질문 안에서 기업을 가리킨 표현 하나와 그 해석 결과.

    canonical이 None이면 옮기지 못한 것이다. 그때 method는 RESOLUTION_AMBIGUOUS_MATCH이고
    candidates에 후보가 전부 들어 있다. 후보 중 하나를 골라 담지 않는다.
    """

    raw: str
    normalized: str
    canonical: str | None
    method: str
    candidates: tuple[str, ...]
    span: tuple[int, int]

    @property
    def resolved(self) -> bool:
        return self.canonical is not None


@dataclass(frozen=True)
class CompanyResolution:
    """
    질문 하나에 대한 기업명 해석 결과 전부.

    mentions는 질문에 나온 순서다. 옮긴 것과 못 옮긴 것을 함께 담는다. 못 옮긴 것을 빼고
    돌려주면 부르는 쪽이 '모호한 표현이 있었다'는 사실을 알 수 없고, 그러면 모호한 기업 하나를
    조용히 버린 채 나머지로 답하게 된다.
    """

    mentions: tuple[CompanyMention, ...] = ()

    @property
    def companies(self) -> tuple[str, ...]:
        """옮겨진 canonical 기업명. 질문에 나온 순서이며 중복은 없다."""
        ordered: dict[str, None] = {}

        for mention in self.mentions:
            if mention.canonical is not None:
                ordered.setdefault(mention.canonical, None)

        return tuple(ordered)

    @property
    def ambiguous(self) -> tuple[CompanyMention, ...]:
        """후보가 둘 이상이라 옮기지 못한 표현."""
        return tuple(mention for mention in self.mentions if mention.canonical is None)

    @property
    def surfaces(self) -> tuple[str, ...]:
        """
        질문에 실제로 적힌 표기와 그 정규화 표기.

        기업명을 '답의 주체'로 걷어내는 자리(주제어 추출, 계약 상대방 판정)가 이 값을 쓴다.
        canonical만 보면 별칭으로 적은 기업명이 걷어내지지 않아, 사용자가 쓴 회사 이름이
        검색 조건이나 주제어로 다시 들어간다.
        """
        ordered: dict[str, None] = {}

        for mention in self.mentions:
            for surface in (mention.raw, mention.normalized):
                if surface:
                    ordered.setdefault(surface, None)

        return tuple(ordered)

    def first_mention(self, canonical: str) -> CompanyMention | None:
        """해당 기업을 처음 가리킨 표현. 없으면 None."""
        for mention in self.mentions:
            if mention.canonical == canonical:
                return mention

        return None


class CompanyResolver:
    """
    기업 표기 -> canonical 기업 해석기.

    한 번 만들어 두고 질문마다 resolve_question을 부른다. 표기 색인을 생성자에서 만들며,
    질문마다 다시 만들지 않는다.
    """

    def __init__(
        self,
        entries: Iterable[CompanyEntry],
        *,
        aliases: Mapping[str, str] | None = None,
    ) -> None:
        # 두 번 훑으므로 먼저 재료로 굳힌다. generator를 그대로 받으면 첫 훑기에서 소진되어
        # 표기 색인이 조용히 비어 버린다.
        materialized = tuple(entries)

        canonical_by_normalized: dict[str, str] = {}
        canonicals: list[str] = []

        for entry in materialized:
            corp_name = unicodedata.normalize("NFC", entry.corp_name or "")

            normalized = normalize_company_name(corp_name)

            if not normalized or normalized in canonical_by_normalized:
                # 같은 기업이 표기만 달리해 목록에 여러 번 들어 있을 수 있다.
                # 그것은 두 기업이 아니다.
                continue

            canonical_by_normalized[normalized] = corp_name
            canonicals.append(corp_name)

        self._canonicals: tuple[str, ...] = tuple(canonicals)
        self._canonical_by_normalized = canonical_by_normalized

        surfaces: dict[str, tuple[int, list[str]]] = {}

        for entry in materialized:
            self._register_entry(surfaces, entry)

        self._surfaces = surfaces

        for surface, canonical in (
            COMPANY_ALIASES if aliases is None else aliases
        ).items():
            self._register_alias(surfaces, surface, canonical)

        # 긴 표기부터 본다. 이름이 서로를 포함하는 경우(한미약품 / 한미)에 한 기업이 둘로
        # 세어지지 않게 하기 위함이다. 길이가 같으면 문자열 순서로 고정해 결과를 재현 가능하게
        # 둔다.
        self._ordered_surfaces: tuple[str, ...] = tuple(
            sorted(surfaces, key=lambda surface: (-len(surface), surface))
        )

    @classmethod
    def from_names(
        cls,
        names: Iterable[str],
        *,
        aliases: Mapping[str, str] | None = None,
    ) -> CompanyResolver:
        """corp_name 목록만으로 만든다. listed_name이 없는 호출자를 위한 입구다."""
        return cls(
            [CompanyEntry(corp_name=name) for name in names if name],
            aliases=aliases,
        )

    @classmethod
    def from_universe(
        cls,
        universe: Iterable[Mapping[str, object]],
        *,
        extra_names: Iterable[str] = (),
        aliases: Mapping[str, str] | None = None,
    ) -> CompanyResolver:
        """
        manifest에서 읽은 universe(`corp_name` + `listed_name`)로 만든다.

        extra_names는 universe에 없는 canonical 기업명을 더한다. 지분공시의 보고자(`flr_nm`)가
        발행회사 목록에 없을 수 있고, 그 이름은 listed_name을 갖지 않는다.
        """
        entries = [
            CompanyEntry(
                corp_name=str(row.get("corp_name") or ""),
                listed_name=(
                    str(row["listed_name"]) if row.get("listed_name") else None
                ),
            )
            for row in universe
        ]

        known = {
            normalize_company_name(entry.corp_name)
            for entry in entries
            if entry.corp_name
        }

        entries.extend(
            CompanyEntry(corp_name=name)
            for name in extra_names
            if name and normalize_company_name(name) not in known
        )

        return cls(entries, aliases=aliases)

    def _register_entry(
        self,
        surfaces: dict[str, tuple[int, list[str]]],
        entry: CompanyEntry,
    ) -> None:
        corp_name = unicodedata.normalize("NFC", entry.corp_name or "")

        canonical = self._canonical_by_normalized.get(normalize_company_name(corp_name))

        if canonical is None:
            return

        self._register(surfaces, corp_name, _TIER_CANONICAL, canonical)

        if entry.listed_name:
            self._register(surfaces, entry.listed_name, _TIER_LISTED_NAME, canonical)

    def _register_alias(
        self,
        surfaces: dict[str, tuple[int, list[str]]],
        surface: str,
        target: str,
    ) -> None:
        canonical = self._canonical_by_normalized.get(normalize_company_name(target))

        # 대상 기업이 지금 코퍼스에 없으면 등록하지 않는다. 이름만 인식되고 문서가 없으면
        # 뒤 단계가 '문서를 못 찾았다'로 답하게 되고, 실제로 일어난 일과 사유가 달라진다.
        if canonical is None:
            return

        self._register(surfaces, surface, _TIER_ALIAS, canonical)

    @staticmethod
    def _register(
        surfaces: dict[str, tuple[int, list[str]]],
        surface: str,
        tier: int,
        canonical: str,
    ) -> None:
        normalized = normalize_company_name(surface)

        if not normalized:
            return

        current = surfaces.get(normalized)

        if current is None:
            surfaces[normalized] = (tier, [canonical])

            return

        current_tier, current_canonicals = current

        if tier < current_tier:
            # 같은 문자열이 더 앞선 갈래에도 있으면 그쪽이 이긴다. 해석 우선순위 그대로다.
            surfaces[normalized] = (tier, [canonical])

        elif tier == current_tier and canonical not in current_canonicals:
            # 같은 갈래에서 두 기업이 같은 표기를 쓰면 고르지 않는다. 후보로 남겨 두고
            # 판정은 resolve_question이 AMBIGUOUS_MATCH로 한다.
            current_canonicals.append(canonical)

    @property
    def canonical_names(self) -> tuple[str, ...]:
        """인식 대상 canonical 기업명 전부. 입력 순서를 유지한다."""
        return self._canonicals

    def resolve_question(self, question: str) -> CompanyResolution:
        """
        질문 하나에서 기업 표현을 전부 찾아 canonical로 옮긴다.

        첫 매치 하나만 돌려주지 않는다. 두 기업을 묻는 질문에서 하나만 고르면 나머지 기업이
        사라진 채로 답이 조립되고, 사라졌다는 사실이 warnings에도 결과에도 남지 않는다.

        두 벌로 훑는다.

            1. 표기 일치  정규화한 질문 문자열에서 등록된 표기를 긴 것부터 찾는다.
                          띄어쓰기나 '(주)'가 달라도 걸린다.
            2. 부분명     1이 집지 않은 자리만 낱말 단위로 본다. 조사를 떼고 후보를 센다.

        2가 1보다 뒤인 것이 규약이다. 앞에 두면 '삼성전자'를 물은 질문에서 '삼성'이 먼저
        모호성으로 걸려, 확정할 수 있던 질문이 막힌다.
        """
        composed = unicodedata.normalize("NFC", question or "")

        normalized, offsets = normalize_company_name_with_offsets(composed)

        if not normalized:
            return CompanyResolution()

        claimed: list[tuple[int, int]] = []
        mentions: list[CompanyMention] = []

        self._scan_exact_surfaces(composed, normalized, offsets, claimed, mentions)

        self._scan_partial_mentions(composed, normalized, offsets, claimed, mentions)

        mentions.sort(key=lambda mention: mention.span)

        return CompanyResolution(mentions=tuple(mentions))

    def _scan_exact_surfaces(
        self,
        composed: str,
        normalized: str,
        offsets: tuple[int, ...],
        claimed: list[tuple[int, int]],
        mentions: list[CompanyMention],
    ) -> None:
        for surface in self._ordered_surfaces:
            span = find_unclaimed_span(normalized, surface, claimed)

            if span is None:
                continue

            tier, canonicals = self._surfaces[surface]

            claimed.append(span)

            mentions.append(
                self._build_mention(
                    composed=composed,
                    offsets=offsets,
                    span=span,
                    normalized=surface,
                    method=_METHOD_BY_TIER[tier],
                    candidates=canonicals,
                )
            )

    def _scan_partial_mentions(
        self,
        composed: str,
        normalized: str,
        offsets: tuple[int, ...],
        claimed: list[tuple[int, int]],
        mentions: list[CompanyMention],
    ) -> None:
        for token_start, token in _tokens(composed):
            token_span = _normalized_span(
                offsets, token_start, token_start + len(token)
            )

            if token_span is None or _overlaps(*token_span, claimed):
                # 이미 표기 일치로 잡힌 자리다. 같은 글자를 두 번 세지 않는다.
                continue

            mention = self._resolve_token(composed, normalized, offsets, token_start, token)

            if mention is None:
                continue

            claimed.append(mention.span)

            mentions.append(mention)

    def _resolve_token(
        self,
        composed: str,
        normalized: str,
        offsets: tuple[int, ...],
        token_start: int,
        token: str,
    ) -> CompanyMention | None:
        """
        낱말 하나를 부분명으로 볼 수 있는지 판정한다.

        낱말 그대로를 먼저 보고, 걸리지 않으면 조사를 뗀 형태를 본다. 조사를 뗀 형태를 먼저
        보면 '기아'로 끝나는 이름에서 마지막 글자가 조사로 오해될 수 있다.
        """
        for candidate in _mention_candidates(token):
            span = _normalized_span(offsets, token_start, token_start + len(candidate))

            if span is None:
                continue

            mention_text = normalized[span[0] : span[1]]

            if len(mention_text) < PARTIAL_MIN_CANDIDATE_LENGTH:
                continue

            registered = self._surfaces.get(mention_text)

            if registered is not None:
                # 같은 표기가 질문에 두 번 나온 경우다. 앞 훑기가 첫 자리를 이미 집었다.
                tier, canonicals = registered

                return self._build_mention(
                    composed=composed,
                    offsets=offsets,
                    span=span,
                    normalized=mention_text,
                    method=_METHOD_BY_TIER[tier],
                    candidates=canonicals,
                )

            if not self._opens_partial_lookup(mention_text):
                continue

            candidates = self._partial_candidates(mention_text)

            if len(candidates) > 1:
                return self._build_mention(
                    composed=composed,
                    offsets=offsets,
                    span=span,
                    normalized=mention_text,
                    method=RESOLUTION_UNIQUE_PARTIAL,
                    candidates=candidates,
                )

            if len(candidates) == 1 and len(mention_text) >= PARTIAL_MIN_RESOLVE_LENGTH:
                return self._build_mention(
                    composed=composed,
                    offsets=offsets,
                    span=span,
                    normalized=mention_text,
                    method=RESOLUTION_UNIQUE_PARTIAL,
                    candidates=candidates,
                )

            # 후보가 하나인데 표현이 짧으면 여기서 멈춘다. 더 짧은 형태를 다시 보아도 근거는
            # 얇아지기만 한다.
            return None

        return None

    def _opens_partial_lookup(self, mention: str) -> bool:
        """
        이 표현으로 부분명 조회를 열어도 되는가.

        등록된 표기 중 하나의 앞부분일 때만 연다. 이 조건이 회사명 안에 우연히 들어가는
        일반 낱말('금융', '기술', '전기', '건설')을 기업 언급에서 제외한다. 후보를 좁히는
        규칙이 아니라 조회 자체를 여는 규칙이므로, 이것으로 후보 중 하나가 골라지지 않는다.
        """
        return any(surface.startswith(mention) for surface in self._surfaces)

    def _partial_candidates(self, mention: str) -> tuple[str, ...]:
        """
        표현을 부분으로 담고 있는 기업 전부. 앞부분이 아니어도 센다.

        넓게 세는 것이 규약이다. 좁게 세면 후보가 줄어 unique가 되고, 그때 옮긴 결과는
        '후보가 하나여서'가 아니라 '세는 규칙이 나머지를 가려서' 나온 것이 된다.
        """
        ordered: dict[str, None] = {}

        for surface in self._ordered_surfaces:
            if mention not in surface:
                continue

            for canonical in self._surfaces[surface][1]:
                ordered.setdefault(canonical, None)

        return tuple(ordered)

    @staticmethod
    def _build_mention(
        *,
        composed: str,
        offsets: tuple[int, ...],
        span: tuple[int, int],
        normalized: str,
        method: str,
        candidates: Sequence[str],
    ) -> CompanyMention:
        resolved = candidates[0] if len(candidates) == 1 else None

        return CompanyMention(
            raw=_raw_slice(composed, offsets, span),
            normalized=normalized,
            canonical=resolved,
            method=method if resolved is not None else RESOLUTION_AMBIGUOUS_MATCH,
            # 후보는 이름 순으로 고정한다. 등록 순서나 표기 길이 순으로 두면 같은 모호성이
            # manifest 정렬에 따라 다른 차례로 보고되고, 그 차례가 '첫 후보'처럼 읽힌다.
            candidates=tuple(sorted(candidates)),
            span=span,
        )


def _raw_slice(
    composed: str,
    offsets: tuple[int, ...],
    span: tuple[int, int],
) -> str:
    """정규화 구간에 대응하는 원문 표기. 사용자가 실제로 쓴 글자 그대로다."""
    start, end = span

    if start >= end or end > len(offsets):
        return ""

    return composed[offsets[start] : offsets[end - 1] + 1]


def _normalized_span(
    offsets: tuple[int, ...],
    start: int,
    end: int,
) -> tuple[int, int] | None:
    """원문 구간 [start, end)에 대응하는 정규화 구간. 남는 글자가 없으면 None."""
    low = bisect.bisect_left(offsets, start)
    high = bisect.bisect_left(offsets, end)

    return (low, high) if low < high else None


def _tokens(composed: str) -> tuple[tuple[int, str], ...]:
    """
    공백으로 나눈 낱말과 그 시작 위치. 양끝 문장부호는 뗀다.

    형태소 분석을 하지 않는다. 이 자리에 필요한 것은 '어디까지가 한 낱말인가'뿐이고,
    그 이상을 추정하면 추정 규칙이 기업 인식 범위를 조용히 바꾼다.
    """
    tokens: list[tuple[int, str]] = []

    for match in _TOKEN_PATTERN.finditer(composed):
        chunk = match.group(0)

        stripped = chunk.strip(_TOKEN_TRIM_CHARS)

        if stripped:
            tokens.append((match.start() + chunk.find(stripped), stripped))

    return tuple(tokens)


def _mention_candidates(token: str) -> tuple[str, ...]:
    """
    낱말 하나에서 만들 수 있는 기업 표현 후보. 낱말 그대로가 먼저다.

    조사는 한 번만 뗀다. 반복해서 떼면 '카카오'에서 '오'·'카카'처럼 이름의 일부가 조사로
    오해되어, 질문에 없는 표현으로 회사를 찾게 된다.
    """
    candidates = [token]

    for particle in MENTION_PARTICLES:
        if len(token) > len(particle) and token.endswith(particle):
            candidates.append(token[: -len(particle)])

            break

    return tuple(candidates)
