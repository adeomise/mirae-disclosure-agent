"""기업명 해석기 단위 테스트.

고정하려는 규약:
    canonical / listed_name / alias / unique_partial 네 경로로만 기업이 확정된다.
    확정할 수 없으면 후보를 남기고 고르지 않는다.

여기서 코퍼스를 읽지 않는 이유:
    해석 규칙은 어떤 기업 목록에서든 같아야 한다. 실제 manifest로 시험하면 코퍼스가 바뀔 때
    규칙이 아니라 데이터 때문에 테스트가 흔들리고, '삼성이 여덟 곳이라 모호하다'가 '지금
    코퍼스에 여덟 곳이 있다'로 바뀐다. 아래 universe는 실제 코퍼스의 모양(같은 접두어를
    공유하는 계열사, canonical과 다른 상장명)만 본떠 만든 것이다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent.company_resolution import (
    PARTIAL_MIN_RESOLVE_LENGTH,
    RESOLUTION_ALIAS,
    RESOLUTION_AMBIGUOUS_MATCH,
    RESOLUTION_CANONICAL,
    RESOLUTION_LISTED_NAME,
    RESOLUTION_UNIQUE_PARTIAL,
    CompanyEntry,
    CompanyResolver,
    normalize_company_name_with_offsets,
)
from disclosure_agent.retrieval.metadata_filter import (
    list_company_universe,
    normalize_company_name,
)

# 실제 코퍼스의 모양을 본뜬 인식 후보.
#
#     삼성*            같은 접두어를 여럿이 공유한다. '삼성' 단독 표현의 모호성이 여기서 온다.
#     현대자동차       canonical과 상장명이 다르다.
#     삼성화재해상보험 상장명이 canonical의 앞부분이다.
#     케이티           상장명이 로마자다.
#     레인보우로보틱스 접두어를 혼자 쓴다. 부분명이 유일하게 확정되는 자리다.
#     한미*            접두어를 둘이 나눠 쓴다. 부분명이 모호한 자리다.
UNIVERSE: tuple[dict[str, str], ...] = (
    {"corp_name": "삼성전자", "listed_name": "삼성전자"},
    {"corp_name": "삼성전기", "listed_name": "삼성전기"},
    {"corp_name": "삼성SDI", "listed_name": "삼성SDI"},
    {"corp_name": "삼성바이오로직스", "listed_name": "삼성바이오로직스"},
    {"corp_name": "삼성화재해상보험", "listed_name": "삼성화재"},
    {"corp_name": "현대자동차", "listed_name": "현대차"},
    {"corp_name": "기아", "listed_name": "기아"},
    {"corp_name": "케이티", "listed_name": "KT"},
    {"corp_name": "레인보우로보틱스", "listed_name": "레인보우로보틱스"},
    {"corp_name": "한미반도체", "listed_name": "한미반도체"},
    {"corp_name": "한미약품", "listed_name": "한미약품"},
    {"corp_name": "LG생활건강", "listed_name": "LG생활건강"},
)


@pytest.fixture
def resolver() -> CompanyResolver:
    return CompanyResolver.from_universe(UNIVERSE)


def _single(resolver: CompanyResolver, question: str):
    """표현 하나만 읽힌 질문의 그 표현. 개수를 함께 고정한다."""
    resolution = resolver.resolve_question(question)

    assert len(resolution.mentions) == 1, resolution.mentions

    return resolution.mentions[0]


# ---------------------------------------------------------------------------
# 정규화: 위치를 함께 남기는 판이 기존 정규화와 같은 문자열을 만드는가
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "삼성전자",
        "(주)한미반도체",
        "한미반도체 주식회사",
        "LG생활건강",
        "삼성 화재 해상 보험",
        "현대차 2025년 매출 알려줘",
        "SK하이닉스·삼성전자",
        "",
    ],
)
def test_offset_aware_normalization_matches_the_shared_rule(text: str) -> None:
    """
    normalize_company_name과 결과가 같아야 한다.

    두 벌이 어긋나면 표기 일치는 한쪽 규칙으로, 부분명은 다른 쪽 규칙으로 판정된다. 그 차이는
    결과만 봐서는 드러나지 않는다.
    """
    normalized, offsets = normalize_company_name_with_offsets(text)

    assert normalized == normalize_company_name(text)

    assert len(offsets) == len(normalized)

    # 위치는 단조증가해야 이분 탐색으로 두 좌표계를 오갈 수 있다.
    assert list(offsets) == sorted(offsets)


# ---------------------------------------------------------------------------
# 정상 resolve
# ---------------------------------------------------------------------------


def test_canonical_name_resolves_to_itself(resolver: CompanyResolver) -> None:
    mention = _single(resolver, "삼성전자 2025년 연결 기준 매출액이 얼마야?")

    assert mention.canonical == "삼성전자"
    assert mention.method == RESOLUTION_CANONICAL
    assert mention.raw == "삼성전자"


@pytest.mark.parametrize(
    "question",
    [
        "(주)한미반도체 2025년 매출은?",
        "한미반도체 주식회사 2025년 매출은?",
        "한미 반도체 2025년 매출은?",
    ],
)
def test_legal_suffix_and_spacing_normalization_still_holds(
    resolver: CompanyResolver,
    question: str,
) -> None:
    """
    기존 표기 정규화가 그대로 남는지 고정한다.

    별칭 규칙을 얹으면서 이 경로가 깨지면, 지금까지 답하던 질문이 조용히 막힌다.
    """
    mention = _single(resolver, question)

    assert mention.canonical == "한미반도체"
    assert mention.method == RESOLUTION_CANONICAL


@pytest.mark.parametrize(
    ("question", "expected", "raw"),
    [
        ("현대차 2025년 매출 알려줘", "현대자동차", "현대차"),
        ("삼성화재 2025년 매출 알려줘", "삼성화재해상보험", "삼성화재"),
        ("KT 2025년 매출 알려줘", "케이티", "KT"),
    ],
)
def test_listed_name_resolves_to_canonical(
    resolver: CompanyResolver,
    question: str,
    expected: str,
    raw: str,
) -> None:
    mention = _single(resolver, question)

    assert mention.canonical == expected
    assert mention.method == RESOLUTION_LISTED_NAME
    assert mention.raw == raw


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("삼바 2025년 매출 알려줘", "삼성바이오로직스"),
        ("기아자동차 2025년 매출 알려줘", "기아"),
    ],
)
def test_registered_alias_resolves_to_canonical(
    resolver: CompanyResolver,
    question: str,
    expected: str,
) -> None:
    """등록된 alias만 옮긴다. 비슷해 보인다는 이유로 옮기는 경로는 없다."""
    mention = _single(resolver, question)

    assert mention.canonical == expected
    assert mention.method == RESOLUTION_ALIAS


def test_unique_partial_name_resolves(resolver: CompanyResolver) -> None:
    mention = _single(resolver, "레인보우 2025년 매출 알려줘")

    assert mention.canonical == "레인보우로보틱스"
    assert mention.method == RESOLUTION_UNIQUE_PARTIAL
    assert mention.candidates == ("레인보우로보틱스",)


def test_alias_and_canonical_mix_in_one_comparison(resolver: CompanyResolver) -> None:
    """
    다중 기업 질문이 그대로 지원되어야 한다.

    별칭 하나가 섞였다고 비교 질문이 단일 기업으로 줄어들면, 사라진 기업이 결과 어디에도
    남지 않는다.
    """
    resolution = resolver.resolve_question("현대차랑 삼성전자 2025년 매출 비교해줘")

    assert resolution.companies == ("현대자동차", "삼성전자")

    assert [mention.method for mention in resolution.mentions] == [
        RESOLUTION_LISTED_NAME,
        RESOLUTION_CANONICAL,
    ]

    assert resolution.ambiguous == ()


def test_resolution_records_provenance(resolver: CompanyResolver) -> None:
    """raw / normalized / canonical / method / candidates가 모두 남아야 한다."""
    mention = _single(resolver, "삼성화재 2025년 매출 알려줘")

    assert mention.raw == "삼성화재"
    assert mention.normalized == "삼성화재"
    assert mention.canonical == "삼성화재해상보험"
    assert mention.method == RESOLUTION_LISTED_NAME
    assert mention.candidates == ("삼성화재해상보험",)
    assert mention.resolved is True


# ---------------------------------------------------------------------------
# fail-close
# ---------------------------------------------------------------------------


def test_partial_name_with_multiple_candidates_is_ambiguous(
    resolver: CompanyResolver,
) -> None:
    """'삼성'을 삼성전자로 고르지 않는다. 가장 유명한 회사를 고르는 규칙은 없다."""
    mention = _single(resolver, "삼성 2025년 매출 알려줘")

    assert mention.canonical is None
    assert mention.method == RESOLUTION_AMBIGUOUS_MATCH
    assert "삼성전자" in mention.candidates
    assert len(mention.candidates) > 1


def test_partial_name_shared_by_two_companies_is_ambiguous(
    resolver: CompanyResolver,
) -> None:
    mention = _single(resolver, "한미 2025년 매출 알려줘")

    assert mention.canonical is None
    assert mention.candidates == ("한미반도체", "한미약품")


def test_partial_substring_inside_several_names_is_ambiguous(
    resolver: CompanyResolver,
) -> None:
    """
    표기의 앞부분이면서 여러 기업 이름 안에 들어 있는 표현.

    '삼성전'은 삼성전자와 삼성전기 둘의 부분이며, 둘 중 하나를 고르지 않는다.
    """
    mention = _single(resolver, "삼성전 2025년 매출 알려줘")

    assert mention.canonical is None
    assert mention.candidates == ("삼성전기", "삼성전자")


def test_unknown_company_produces_no_mention(resolver: CompanyResolver) -> None:
    resolution = resolver.resolve_question("없는회사 2025년 매출 알려줘")

    assert resolution.mentions == ()
    assert resolution.companies == ()
    assert resolution.ambiguous == ()


def test_ambiguous_mention_is_reported_next_to_a_resolved_one(
    resolver: CompanyResolver,
) -> None:
    """
    모호한 표현을 조용히 버리지 않는다.

    버리면 '삼성이랑 현대차 비교해줘'가 현대자동차 단독 질문이 되고, 질문이 지목한 기업 하나가
    사라진 사실이 결과에 남지 않는다.
    """
    resolution = resolver.resolve_question("삼성이랑 현대차 비교해줘")

    assert resolution.companies == ("현대자동차",)

    assert [mention.raw for mention in resolution.ambiguous] == ["삼성"]


def test_short_unique_partial_is_not_resolved(resolver: CompanyResolver) -> None:
    """
    후보가 하나여도 표현이 너무 짧으면 옮기지 않는다.

    '레인'은 레인보우로보틱스 하나에만 걸리지만 두 글자다. 이 근거로 기업을 확정하면 두 글자
    일반 낱말이 회사 이름의 앞부분과 겹칠 때마다 질문에 없던 회사가 답에 들어온다.
    """
    assert len("레인") < PARTIAL_MIN_RESOLVE_LENGTH

    resolution = resolver.resolve_question("레인 2025년 매출 알려줘")

    assert resolution.mentions == ()


def test_common_word_inside_company_names_is_not_a_mention(
    resolver: CompanyResolver,
) -> None:
    """
    회사명 안에 들어 있지만 어느 표기의 앞부분도 아닌 낱말은 기업 언급이 아니다.

    '반도체'는 한미반도체 안에 있으나 앞부분이 아니다. 이런 낱말까지 기업 언급으로 읽으면
    답할 수 있던 질문이 모호성으로 막힌다.
    """
    resolution = resolver.resolve_question("삼성전자 2025년 반도체 매출 알려줘")

    assert resolution.companies == ("삼성전자",)
    assert resolution.ambiguous == ()


def test_longer_name_is_not_counted_twice(resolver: CompanyResolver) -> None:
    """'한미약품'을 물은 질문에서 '한미'가 다시 걸려 두 기업이 되지 않는다."""
    resolution = resolver.resolve_question("한미약품 2025년 매출 알려줘")

    assert resolution.companies == ("한미약품",)
    assert resolution.ambiguous == ()


# ---------------------------------------------------------------------------
# alias 등록 정책
# ---------------------------------------------------------------------------


def test_alias_colliding_with_two_companies_is_ambiguous() -> None:
    """
    같은 표기가 두 canonical company를 가리키도록 등록되면 고르지 않는다.

    alias 표는 사람이 관리한다. 충돌을 넣을 수 있으므로 충돌한 채로 답이 나가지 않게 한다.
    """
    resolver = CompanyResolver.from_universe(
        UNIVERSE,
        aliases={"에스디아이": "삼성SDI", "에스 디 아이": "삼성전자"},
    )

    mention = _single(resolver, "에스디아이 2025년 매출 알려줘")

    assert mention.canonical is None
    assert mention.method == RESOLUTION_AMBIGUOUS_MATCH
    assert mention.candidates == ("삼성SDI", "삼성전자")


def test_alias_for_a_company_outside_the_corpus_is_not_registered() -> None:
    """
    코퍼스에 없는 회사로 가는 alias는 등록하지 않는다.

    등록하면 이름만 인식되고 문서가 없어 '문서를 못 찾았다'로 답하게 된다. 실제로 일어난 일은
    '그 회사는 여기 없다'이며, 두 사유는 다르다.
    """
    resolver = CompanyResolver.from_universe(
        UNIVERSE,
        aliases={"엘지전자": "LG전자"},
    )

    assert resolver.resolve_question("엘지전자 2025년 매출 알려줘").mentions == ()


def test_canonical_name_wins_over_a_conflicting_alias() -> None:
    """같은 문자열이 canonical과 alias 양쪽에 있으면 canonical이 먼저다(해석 우선순위 1 > 4)."""
    resolver = CompanyResolver.from_universe(
        UNIVERSE,
        aliases={"기아": "삼성전자"},
    )

    mention = _single(resolver, "기아 2025년 매출 알려줘")

    assert mention.canonical == "기아"
    assert mention.method == RESOLUTION_CANONICAL


def test_listed_name_wins_over_a_conflicting_alias() -> None:
    """상장명과 alias가 같은 문자열이면 상장명이 먼저다(해석 우선순위 3 > 4)."""
    resolver = CompanyResolver.from_universe(
        UNIVERSE,
        aliases={"현대차": "기아"},
    )

    mention = _single(resolver, "현대차 2025년 매출 알려줘")

    assert mention.canonical == "현대자동차"
    assert mention.method == RESOLUTION_LISTED_NAME


def test_resolver_without_listed_names_still_matches_canonical_names() -> None:
    """
    corp_name 목록만 준 호출자의 동작이 그대로여야 한다.

    상장명을 주지 않았으므로 '현대차'는 인식되지 않는다. 그 표기를 코드가 스스로 만들어 내지
    않는다는 뜻이다.
    """
    resolver = CompanyResolver.from_names(["현대자동차", "삼성전자"])

    assert resolver.resolve_question("현대자동차 매출").companies == ("현대자동차",)

    assert resolver.resolve_question("현대차 매출").mentions == ()


def test_extra_names_join_the_universe() -> None:
    """
    universe에 없는 canonical 이름을 더할 수 있어야 한다.

    지분공시의 보고자(flr_nm)가 발행회사 목록에 없을 수 있고, 그 이름은 상장명을 갖지 않는다.
    """
    resolver = CompanyResolver.from_universe(UNIVERSE, extra_names=["국민연금공단"])

    mention = _single(resolver, "국민연금공단 대량보유 공시")

    assert mention.canonical == "국민연금공단"
    assert mention.method == RESOLUTION_CANONICAL


def test_duplicate_entries_are_one_company() -> None:
    """같은 기업이 표기만 달리해 목록에 여러 번 들어 있어도 두 기업이 아니다."""
    resolver = CompanyResolver(
        [
            CompanyEntry(corp_name="한미반도체"),
            CompanyEntry(corp_name="(주)한미반도체"),
        ]
    )

    assert resolver.canonical_names == ("한미반도체",)

    assert resolver.resolve_question("한미반도체 매출").companies == ("한미반도체",)


# ---------------------------------------------------------------------------
# manifest -> universe
# ---------------------------------------------------------------------------


def test_company_universe_keeps_corp_name_and_listed_name() -> None:
    records = [
        {"corp_name": "현대자동차", "listed_name": "현대차"},
        {"corp_name": "현대자동차", "listed_name": "현대차"},
        {"corp_name": "삼성전자", "listed_name": "삼성전자"},
    ]

    assert list_company_universe(records) == (
        {"corp_name": "현대자동차", "listed_name": "현대차"},
        {"corp_name": "삼성전자", "listed_name": "삼성전자"},
    )


def test_conflicting_listed_names_are_dropped() -> None:
    """
    같은 기업에 상장명이 엇갈리면 하나를 고르지 않는다.

    고르면 고른 쪽이 조용히 인식 표기가 되고, 어느 표기로 인식했는지가 결과에 남지 않는다.
    """
    records = [
        {"corp_name": "현대자동차", "listed_name": "현대차"},
        {"corp_name": "현대자동차", "listed_name": "현대자동차주식회사"},
    ]

    assert list_company_universe(records) == (
        {"corp_name": "현대자동차", "listed_name": None},
    )
