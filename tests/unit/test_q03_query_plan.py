"""Focused Q03 query-plan role boundaries."""

import pytest

from disclosure_agent.agent.query_analyzer import (
    INTENT_HOLDING_FACT,
    UnsupportedQuestionError,
    build_query_plan,
)

KNOWN_COMPANIES = ("테스트보고자", "테스트발행회사")


def test_holding_plan_assigns_roles_for_supported_filer_first_wording() -> None:
    plan = build_query_plan(
        "테스트보고자가 2023년 1월에 낸 대량보유 보고서 기준으로, "
        "테스트발행회사 보유주식 수와 보유비율이 얼마야?",
        known_companies=KNOWN_COMPANIES,
    )

    assert plan.intent == INTENT_HOLDING_FACT
    assert plan.filer == "테스트보고자"
    assert plan.companies == ("테스트발행회사",)


def test_holding_plan_fails_closed_for_reversed_company_order() -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "테스트발행회사에 대해 테스트보고자가 2023년 1월에 낸 대량보유 "
            "보고서 기준으로, 보유주식 수와 보유비율이 얼마야?",
            known_companies=KNOWN_COMPANIES,
        )

    assert error.value.reason == "ambiguous_holding_entities"
