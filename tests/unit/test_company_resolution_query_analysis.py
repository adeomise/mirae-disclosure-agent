"""Query Analyzer 기업명 해석 회귀.

test_company_resolution.py와 나누는 이유:
    그쪽은 해석기 혼자의 규칙을 본다. 여기서는 그 결과가 QueryPlan까지 canonical identity로
    이어지는지, 그리고 확정하지 못한 표현이 질의 해석 계층의 거절로 옮겨지는지를 본다.
    해석기가 옳아도 계획이 사용자 표기를 그대로 담으면 Retrieval이 그 이름으로 문서를 찾는다.

evaluation testset과의 관계:
    아래 여섯 갈래가 그대로 evaluation case 유형이다.

        alias success / listed-name success / unique partial success
        ambiguous partial -> ambiguous_company_match
        unknown company -> unknown_company
        mixed exact/alias multi-company comparison

    사유 문자열을 여기서 고정해 두면 testset 쪽이 같은 이름으로 기대값을 적을 수 있다.
"""

from __future__ import annotations

import pytest

from disclosure_agent.agent import company_resolution
from disclosure_agent.agent.company_resolution import (
    RESOLUTION_ALIAS,
    RESOLUTION_CANONICAL,
    RESOLUTION_LISTED_NAME,
    RESOLUTION_UNIQUE_PARTIAL,
)
from disclosure_agent.agent.query_analyzer import (
    AMBIGUOUS_COMPANY_REASON,
    INTENT_CROSS_ENTITY_COMPARE,
    INTENT_DOCUMENT_SUMMARY,
    INTENT_SINGLE_FACT,
    UnsupportedQuestionError,
    _business_profile_residual,
    build_query_plan,
    resolve_companies,
)
from disclosure_agent.common.enums import SectionScope

# tests/unit/test_company_resolution.py와 같은 모양의 인식 후보다. 실제 코퍼스를 읽지 않는
# 이유도 같다.
UNIVERSE: tuple[dict[str, str], ...] = (
    {"corp_name": "삼성전자", "listed_name": "삼성전자"},
    {"corp_name": "삼성전기", "listed_name": "삼성전기"},
    {"corp_name": "삼성바이오로직스", "listed_name": "삼성바이오로직스"},
    {"corp_name": "삼성화재해상보험", "listed_name": "삼성화재"},
    {"corp_name": "현대자동차", "listed_name": "현대차"},
    {"corp_name": "기아", "listed_name": "기아"},
    {"corp_name": "케이티", "listed_name": "KT"},
    {"corp_name": "레인보우로보틱스", "listed_name": "레인보우로보틱스"},
    {"corp_name": "한미반도체", "listed_name": "한미반도체"},
    {"corp_name": "한미약품", "listed_name": "한미약품"},
    {"corp_name": "한화오션", "listed_name": "한화오션"},
)

KNOWN_COMPANIES: tuple[str, ...] = tuple(row["corp_name"] for row in UNIVERSE)


def _plan(question: str):
    return build_query_plan(
        question,
        known_companies=KNOWN_COMPANIES,
        company_universe=UNIVERSE,
    )


def _blocked(question: str) -> UnsupportedQuestionError:
    with pytest.raises(UnsupportedQuestionError) as error:
        _plan(question)

    return error.value


# ---------------------------------------------------------------------------
# 정상 resolve: 무엇으로 적었든 계획에는 canonical만 담긴다
# ---------------------------------------------------------------------------


def test_canonical_exact_still_works() -> None:
    plan = _plan("삼성전자 2025년 연결 기준 매출액이 얼마야?")

    assert plan.companies == ("삼성전자",)
    assert plan.intent == INTENT_SINGLE_FACT


@pytest.mark.parametrize(
    "question",
    [
        "(주)한미반도체 2025년 연결 기준 매출액이 얼마야?",
        "한미반도체 주식회사 2025년 연결 기준 매출액이 얼마야?",
    ],
)
def test_legal_suffix_normalization_still_works(question: str) -> None:
    """별칭 규칙을 얹으면서 기존 표기 정규화가 깨지지 않았는지 고정한다."""
    assert _plan(question).companies == ("한미반도체",)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("현대차 2025년 연결 기준 매출액이 얼마야?", "현대자동차"),
        ("삼성화재 2025년 연결 기준 매출액이 얼마야?", "삼성화재해상보험"),
        ("KT 2025년 연결 기준 매출액이 얼마야?", "케이티"),
    ],
)
def test_listed_name_reaches_the_plan_as_canonical(question: str, expected: str) -> None:
    """
    Retrieval이 보는 이름은 언제나 canonical이어야 한다.

    사용자 표기를 그대로 담으면 '현대차'로 manifest를 뒤지게 되고, 문서가 있는데도 없는 것으로
    답이 나간다.
    """
    assert _plan(question).companies == (expected,)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("삼바 2025년 연결 기준 매출액이 얼마야?", "삼성바이오로직스"),
        ("기아자동차 2025년 연결 기준 매출액이 얼마야?", "기아"),
    ],
)
def test_registered_alias_reaches_the_plan_as_canonical(
    question: str,
    expected: str,
) -> None:
    assert _plan(question).companies == (expected,)


def test_unique_partial_reaches_the_plan_as_canonical() -> None:
    assert _plan("레인보우 2025년 연결 기준 매출액이 얼마야?").companies == (
        "레인보우로보틱스",
    )


def test_mixed_exact_and_alias_comparison_keeps_both_companies() -> None:
    """
    다중 기업 비교가 그대로 지원되어야 한다.

    별칭 하나가 섞였다고 기업 하나가 사라지면, 사라진 사실이 결과에 남지 않은 채로 답이
    조립된다.
    """
    plan = _plan("현대차랑 삼성전자 2025년 매출 비교해줘")

    assert plan.companies == ("현대자동차", "삼성전자")
    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


# ---------------------------------------------------------------------------
# fail-close
# ---------------------------------------------------------------------------


def test_ambiguous_partial_is_blocked_with_its_own_reason() -> None:
    """
    '삼성'을 삼성전자로 고르지 않는다.

    사유를 unknown_company와 나누는 것이 규약이다. 묶으면 여덟 곳이 있는 질문에 '그런 기업
    없음'이라고 답하게 된다.
    """
    error = _blocked("삼성 2025년 연결 기준 매출액이 얼마야?")

    assert error.reason == AMBIGUOUS_COMPANY_REASON
    assert "삼성전자" in error.detail


def test_partial_shared_by_two_companies_is_blocked() -> None:
    error = _blocked("한미 2025년 연결 기준 매출액이 얼마야?")

    assert error.reason == AMBIGUOUS_COMPANY_REASON
    assert "한미반도체" in error.detail
    assert "한미약품" in error.detail


def test_partial_substring_inside_several_names_is_blocked() -> None:
    error = _blocked("삼성전 2025년 연결 기준 매출액이 얼마야?")

    assert error.reason == AMBIGUOUS_COMPANY_REASON


def test_unknown_company_still_reports_unknown_company() -> None:
    error = _blocked("없는회사 2025년 연결 기준 매출액이 얼마야?")

    assert error.reason == "unknown_company"


@pytest.mark.parametrize(
    "question",
    [
        # 'KT'는 케이티의 상장명이지만 'SKT'는 다른 회사다.
        "SKT 2025년 연결 기준 매출액이 얼마야?",
        "KTX 2025년 연결 기준 매출액이 얼마야?",
        # '&'·'+'는 이름을 잇는 기호다. 'KT&G'는 케이티가 아니며 이 UNIVERSE에 없다.
        "KT&G 2025년 연결 기준 매출액이 얼마야?",
        "KT+G 2025년 연결 기준 매출액이 얼마야?",
        # '기아'는 canonical 기업명이지만 야구단은 그 회사가 아니다.
        "기아타이거즈 2025년 연결 기준 매출액이 얼마야?",
    ],
)
def test_embedded_company_spelling_is_blocked(question: str) -> None:
    """
    더 긴 낱말 안에 들어 있는 등록 표기로 계획을 만들지 않는다.

    만들면 질문에 없던 회사의 Fact가 근거까지 갖춘 채 답이 된다. 개수도 형식도 정상이라
    결과만 봐서는 드러나지 않으며, 그래서 계획 이전에 막는다.
    """
    assert _blocked(question).reason == "unknown_company"


def test_valid_spelling_next_to_an_embedded_one_still_builds_a_plan() -> None:
    """경계 검사가 뒤에 있는 올바른 표기까지 막지는 않아야 한다."""
    assert _plan("SKT 말고 KT 2025년 연결 기준 매출액이 얼마야?").companies == ("케이티",)


def test_ambiguous_alias_registry_blocks_the_question(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    alias가 복수 canonical company에 충돌하면 질문을 막는다.

    alias 표는 사람이 관리하므로 충돌이 들어올 수 있다. 해석기 쪽 테스트가 후보 판정을
    고정하고, 여기서는 그 판정이 질의 해석 계층의 거절로 옮겨지는지를 본다.
    """
    monkeypatch.setattr(
        company_resolution,
        "COMPANY_ALIASES",
        {"전자부품": "삼성전자", "전자 부품": "삼성전기"},
    )

    error = _blocked("전자부품 2025년 연결 기준 매출액이 얼마야?")

    assert error.reason == AMBIGUOUS_COMPANY_REASON
    assert "삼성전자" in error.detail
    assert "삼성전기" in error.detail


def test_ambiguous_company_blocks_the_whole_comparison() -> None:
    """
    모호한 표현이 하나라도 있으면 일부만 실행하지 않는다.

    현대자동차만 남겨 답하면 질문이 지목한 기업 하나가 사라지고, 사라진 사실이 결과에 남지
    않는다.
    """
    error = _blocked("삼성이랑 현대차 2025년 매출 비교해줘")

    assert error.reason == AMBIGUOUS_COMPANY_REASON


def test_short_unique_partial_is_not_silently_resolved() -> None:
    """
    후보가 하나여도 두 글자 표현으로는 기업을 확정하지 않는다.

    '레인'은 레인보우로보틱스 하나에만 걸리지만, 이 근거로 확정하면 질문에 없던 회사가 답에
    들어오는 길이 열린다.
    """
    error = _blocked("레인 2025년 연결 기준 매출액이 얼마야?")

    assert error.reason == "unknown_company"


def test_short_unique_partial_blocks_a_mixed_company_question() -> None:
    """
    확정하지 않은 표현이 다른 기업과 섞이면 질문 전체를 막는다.

    이 자리가 이번 회귀의 본체다. '레인'을 버리고 현대자동차 하나로 계획을 만들면, 두 기업을
    물은 질문이 단일 기업 질문이 되고 사라진 기업이 결과 어디에도 남지 않는다. '짧은 부분명은
    자동으로 옮기지 않는다'는 규약이 '없던 일로 한다'로 바뀐 것이며, 그 차이는 답의 모양만
    봐서는 드러나지 않는다.
    """
    error = _blocked("레인이랑 현대차 2025년 매출 비교해줘")

    assert error.reason == "unknown_company"

    # 어느 표현이 걸렸는지가 남아야 한다. 기업을 하나도 못 찾은 경우와 구분되는 자리다.
    assert "레인" in error.detail


def test_long_enough_partial_next_to_another_company_still_compares() -> None:
    """
    해석 하한을 넘는 부분명은 다중 기업 비교에서 그대로 살아 있어야 한다.

    막는 쪽으로 틀리는 것도 틀리는 것이다. 하한에 걸린 표현만 막혀야 하고, 확정할 수 있는
    부분명까지 함께 막히면 답할 수 있던 비교 질문이 사라진다.
    """
    plan = _plan("레인보우랑 현대차 2025년 매출 비교해줘")

    assert plan.companies == ("레인보우로보틱스", "현대자동차")
    assert plan.intent == INTENT_CROSS_ENTITY_COMPARE


# ---------------------------------------------------------------------------
# 사용자 표기가 '답의 주체'로 걷어내지는가
#
# canonical 이름만 걷어내면 별칭·상장명으로 적은 기업명이 조회 조건 자리에 남는다. 그 자리가
# 둘이며(요약 주제어, 계약 상대방) 둘 다 canonical 표기로 물었을 때와 같아야 한다.
# ---------------------------------------------------------------------------


def test_alias_spelling_is_not_read_as_an_event_subject() -> None:
    """
    '현대차 시설투자 공시 내용 요약해줘'의 주제어는 '시설투자' 하나여야 한다.

    '현대차'가 주제어로 남으면 답의 주체가 이벤트를 가르는 조건이 되고, 그 낱말이 본문에 없는
    문서가 전부 탈락한다.
    """
    canonical = _plan("현대자동차 시설투자 공시 내용 요약해줘")

    alias = _plan("현대차 시설투자 공시 내용 요약해줘")

    assert alias.companies == canonical.companies == ("현대자동차",)

    assert alias.event_subject_tokens == canonical.event_subject_tokens == ("시설투자",)


def test_listed_name_spelling_is_not_read_as_a_counterparty() -> None:
    """
    상장명으로 적은 대상 기업을 계약 상대방으로 담지 않는다.

    담으면 그 이름으로 후보를 좁혀 아무 문서도 찾지 못한다. canonical 표기로 물었을 때 이미
    막던 자리이며, 표기가 달라졌다고 열리면 안 된다.
    """
    plan = _plan("KT와 체결한 2025년 계약의 계약금액과 계약기간은?")

    assert plan.companies == ("케이티",)

    assert plan.counterparty is None


# ---------------------------------------------------------------------------
# 기존 계약 유지
# ---------------------------------------------------------------------------


def test_known_companies_alone_still_builds_a_plan() -> None:
    """
    universe를 주지 않는 기존 호출이 그대로 동작해야 한다.

    상장명이 없으므로 '현대차'는 인식되지 않는다. 없는 표기를 코드가 스스로 만들지 않는다는
    뜻이며, 그 경계가 흐려지면 어디까지가 manifest의 사실인지 알 수 없게 된다.
    """
    plan = build_query_plan(
        "삼성전자 2025년 연결 기준 매출액이 얼마야?",
        known_companies=KNOWN_COMPANIES,
    )

    assert plan.companies == ("삼성전자",)

    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "현대차 2025년 연결 기준 매출액이 얼마야?",
            known_companies=KNOWN_COMPANIES,
        )

    assert error.value.reason == "unknown_company"


def test_missing_company_universe_is_still_rejected() -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan("삼성전자 2025년 매출액이 얼마야?")

    assert error.value.reason == "missing_company_universe"


# ---------------------------------------------------------------------------
# provenance
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("question", "raw", "canonical", "method"),
    [
        ("삼성전자 2025년 매출", "삼성전자", "삼성전자", RESOLUTION_CANONICAL),
        ("현대차 2025년 매출", "현대차", "현대자동차", RESOLUTION_LISTED_NAME),
        ("삼바 2025년 매출", "삼바", "삼성바이오로직스", RESOLUTION_ALIAS),
        ("레인보우 2025년 매출", "레인보우", "레인보우로보틱스", RESOLUTION_UNIQUE_PARTIAL),
    ],
)
def test_resolution_provenance_is_available_without_a_plan(
    question: str,
    raw: str,
    canonical: str,
    method: str,
) -> None:
    """
    어느 표현이 어떤 경로로 어느 회사가 되었는지를 QueryPlan을 거치지 않고 볼 수 있어야 한다.

    QueryPlan에는 담지 않는다. Contract를 넓히는 일이라 이번 범위 밖이며, 디버깅에 필요한 것은
    계획이 아니라 해석 근거다.
    """
    resolution = resolve_companies(
        question,
        known_companies=KNOWN_COMPANIES,
        company_universe=UNIVERSE,
    )

    assert [mention.raw for mention in resolution.mentions] == [raw]
    assert resolution.companies == (canonical,)
    assert resolution.mentions[0].method == method


# --------------------------------------------------------------------------
# 기업 소개 요약(Q05)과 사용자 표기
# --------------------------------------------------------------------------
#
# Q05는 "이 질문에 답하지 않을 요청이 남았는가"를 세어 경로를 연다. 세려면 질문에서 기업을
# 가리킨 말을 걷어내야 하고, 그 말은 canonical이 아니라 사용자가 쓴 표기다. canonical만
# 걷어내면 '현대차'·'삼바'가 남은 요청으로 세어져 정상 Q05가 막힌다.

Q05_ANCHORS = dict.fromkeys(KNOWN_COMPANIES, 2025)


def _profile_plan(question: str):
    return build_query_plan(
        question,
        known_companies=KNOWN_COMPANIES,
        company_universe=UNIVERSE,
        fiscal_year_anchors=Q05_ANCHORS,
    )


@pytest.mark.parametrize(
    ("question", "canonical"),
    [
        # canonical 표기 그대로.
        ("현대자동차는 무슨 사업을 하고 대표 제품이 뭐야?", "현대자동차"),
        # 상장명. manifest가 아는 표기이며 canonical과 글자가 다르다.
        ("현대차는 무슨 사업을 하고 대표 제품이 뭐야?", "현대자동차"),
        ("삼성화재는 어떤 사업을 하고 주요한 제품이 뭐야?", "삼성화재해상보험"),
        ("KT는 무슨 사업을 하고 대표 제품이 뭐야?", "케이티"),
        # 등록된 별칭.
        ("삼바는 무슨 사업을 하고 대표 제품이 뭐야?", "삼성바이오로직스"),
        # 유일하게 걸리는 부분명.
        ("레인보우는 무슨 사업을 하고 주력 제품이 뭐야?", "레인보우로보틱스"),
    ],
)
def test_business_profile_opens_for_every_recognized_company_wording(
    question: str,
    canonical: str,
) -> None:
    """
    어느 표기로 물었든 같은 Q05 계획이 된다.

    계획에 담기는 이름은 canonical 하나다. 사용자 표기가 계획으로 새어 나가면 Retrieval이
    그 이름으로 문서를 찾고, 상장명·별칭으로 물은 질문이 조용히 빈 결과가 된다.
    """
    plan = _profile_plan(question)

    assert plan.intent == INTENT_DOCUMENT_SUMMARY
    assert plan.companies == (canonical,)
    assert plan.section_scopes == (
        SectionScope.BUSINESS_OVERVIEW,
        SectionScope.PRODUCTS,
    )


@pytest.mark.parametrize(
    ("question", "surviving"),
    [
        ("현대차는 무슨 사업을 하고 대표 제품 출시일도 알려줘", "출시일"),
        ("삼바는 무슨 사업을 하고 대표 제품이 뭐고 생산량도 알려줘", "생산량"),
        ("현대차는 어떤 사업을 하고 어떤 제품이 주력인지와 가격도 알려줘", "가격"),
        ("KT는 무슨 사업을 하고 핵심 제품 원가 알려줘", "원가"),
        ("삼성화재는 어떤 사업을 하고 주력 제품 납기도 알려줘", "납기"),
    ],
)
def test_user_wording_does_not_hide_a_remaining_request(
    question: str,
    surviving: str,
) -> None:
    """
    사용자 표기를 걷어내는 일이 남은 요청까지 함께 걷어내지는 않는다.

    걷어내는 대상을 넓히면 반대쪽으로 틀릴 수 있다. 상장명·별칭을 지우느라 질문이 함께 물은
    속성까지 사라지면, 이번에는 그 요청이 조용히 요약으로 축소된다. 두 방향을 함께 고정한다.
    """
    resolution = resolve_companies(
        question,
        known_companies=KNOWN_COMPANIES,
        company_universe=UNIVERSE,
    )

    residual = _business_profile_residual(
        question,
        resolution.companies,
        resolution.surfaces,
    )

    assert surviving in residual

    with pytest.raises(UnsupportedQuestionError) as error:
        _profile_plan(question)

    assert error.value.reason == "missing_year"
