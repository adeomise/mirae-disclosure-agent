"""상대 기간("작년")을 절대연도로 옮기는 규칙을 검증한다.

무엇을 고정하는가 (docs/agent_design/open_decisions.md D8):
    1. 기준 연도(anchor)는 해당 기업의 사업보고서가 코퍼스에 존재하는 가장 최신 회계연도다.
    2. 실행 시각·현재 연도를 쓰지 않는다.
    3. 요청 지표를 그 문서에서 읽을 수 있는지로 기준 연도를 바꾸지 않는다.
    4. 과거 방향 한 해만 옮긴다. 미래 방향은 Pattern F 소관이다.
    5. 지원하지 않는 표현은 해석하지 않고 거절한다.

왜 3번이 따로 필요한가:
    "2025년 매출을 못 읽었으니 2024년을 최신으로 치자"는 형식상 아무 문제가 없어 보인다.
    그런데 그 결과는 근거를 갖춘 채로 질문과 다른 기간을 답하는 형태이고, 개수로도 형식으로도
    드러나지 않는다. 추출 실패는 NOT_FOUND로 보이지만 기준 연도가 밀린 것은 정답처럼 보인다.

왜 5번이 missing_year와 달라야 하는가:
    "재작년보다 얼마나 늘었어?"에는 기간이 분명히 적혀 있다. 못 읽은 것은 연도가 아니라 그
    표현을 옮기는 규칙이다. 사유가 실제로 일어난 일과 다르면 무엇을 구현하면 풀리는 질문인지가
    결과에서 사라진다.
"""

from __future__ import annotations

import ast
import inspect
import os
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent.query_analyzer import (
    ANCHOR_SOURCE_LATEST_ANNUAL_FILING,
    INTENT_TEMPORAL_COMPARE,
    METRIC_REVENUE,
    PREVIOUS_YEAR_EXPRESSIONS,
    PREVIOUS_YEAR_OFFSET,
    RelativePeriod,
    UnsupportedQuestionError,
    build_query_plan,
)
from disclosure_agent.retrieval import metadata_filter
from disclosure_agent.retrieval.metadata_filter import (
    latest_annual_fiscal_year,
    latest_annual_fiscal_year_by_company,
    list_company_names,
    load_manifest,
)

KNOWN_COMPANIES = ("한미반도체", "삼성전자", "테스트반도체(주)")

ANCHORS = {"한미반도체": 2025, "삼성전자": 2025}

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "src" / "disclosure_agent"


def annual(company: str, base_year: Any, **overrides: Any) -> dict[str, Any]:
    """정기공시·사업보고서 manifest 레코드 하나. 값은 전부 합성이다."""
    record: dict[str, Any] = {
        "corp_name": company,
        "doc_group": "periodic",
        "doc_subtype": "annual",
        "base_year": base_year,
        "is_correction": False,
        "rcept_no": f"SYNTHETIC{company}{base_year}",
        "rcept_dt": "20260312",
    }

    record.update(overrides)

    return record


# --------------------------------------------------------------------------
# 1. 기준 연도(anchor) 유도
# --------------------------------------------------------------------------


def test_anchor_is_the_latest_annual_fiscal_year() -> None:
    """사업보고서가 있는 회계연도 중 가장 최신을 고른다."""
    records = [
        annual("한미반도체", 2023),
        annual("한미반도체", 2024),
        annual("한미반도체", 2025),
    ]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2025


def test_anchor_uses_an_older_year_only_when_no_newer_filing_exists() -> None:
    """
    FY2025 사업보고서가 없으면 anchor는 FY2024다.

    '문서가 없어서 2024'와 '값을 못 읽어서 2024'는 다르다. 이 케이스가 고정하는 것은 앞쪽
    하나뿐이며, 뒤쪽은 아래 test_anchor_ignores_metric_availability가 막는다.
    """
    records = [annual("한미반도체", 2023), annual("한미반도체", 2024)]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2024


def test_quarterly_filing_of_a_newer_year_is_not_an_anchor() -> None:
    """
    분기·반기보고서는 기준 연도를 올리지 않는다.

    실측 코퍼스가 정확히 이 모양이다. 70개사 전부 FY2026 분기보고서가 있고 사업보고서는
    FY2025가 최신이다. 문서군을 좁히지 않고 base_year의 최댓값을 취하면 anchor가 2026이 되어
    존재하지 않는 FY2026 사업보고서를 찾다가 실패한다.
    """
    records = [
        annual("한미반도체", 2025),
        annual("한미반도체", 2026, doc_subtype="quarter", base_month=3),
        annual("한미반도체", 2026, doc_subtype="half", base_month=6),
    ]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2025


def test_event_filing_of_a_newer_year_is_not_an_anchor() -> None:
    """거래소공시도 기준 연도를 올리지 않는다. 문서군이 다르다."""
    records = [
        annual("한미반도체", 2025),
        annual("한미반도체", 2026, doc_group="exchange", doc_subtype="단일판매공급계약체결"),
    ]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2025


def test_correction_only_fiscal_year_still_counts_as_an_anchor() -> None:
    """
    정정본만 있는 회계연도도 기준 연도가 된다.

    여기서 묻는 것은 "그 해의 사업보고서가 코퍼스에 있는가"이지 "어느 판본을 읽을 것인가"가
    아니다. 판본 선택은 D5이며 이 함수가 정하지 않는다.

    실측 코퍼스에 이 모양이 실제로 있다. 한 기업의 FY2025 사업보고서가 정정본 한 건뿐이며,
    정정본을 제외하면 그 기업만 anchor가 2024가 되고 나머지는 2025가 된다. 같은 질문이 기업에
    따라 다른 해를 가리키는 것은 정책이 아니라 사고다.
    """
    records = [
        annual("한화솔루션", 2024),
        annual("한화솔루션", 2025, is_correction=True),
    ]

    assert latest_annual_fiscal_year(records, company="한화솔루션") == 2025


def test_anchor_is_resolved_per_company() -> None:
    """다른 기업의 사업보고서가 기준 연도를 올리지 않는다."""
    records = [annual("한미반도체", 2024), annual("삼성전자", 2025)]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2024
    assert latest_annual_fiscal_year(records, company="삼성전자") == 2025


def test_anchor_matches_legal_suffix_variants() -> None:
    """법인격 표기가 붙어도 같은 기업으로 찾는다."""
    records = [annual("한미반도체(주)", 2025)]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2025
    assert latest_annual_fiscal_year(records, company="한미반도체(주)") == 2025


def test_anchor_is_none_when_no_annual_filing_exists() -> None:
    """사업보고서가 없으면 추측하지 않고 None이다."""
    records = [annual("한미반도체", 2025, doc_subtype="quarter")]

    assert latest_annual_fiscal_year(records, company="한미반도체") is None
    assert latest_annual_fiscal_year([], company="한미반도체") is None


def test_anchor_skips_records_without_an_integer_fiscal_year() -> None:
    """
    base_year 표기가 정수가 아니면 건너뛴다.

    문자열을 정수로 바꿔 받아들이지 않는다. 표기가 흔들리는 manifest에서 기준 연도를 만들어
    내는 것보다 기준 없이 막히는 쪽이 낫다. bool을 함께 배제하는 것은 isinstance(True, int)가
    참이라 회계연도 자리에 True가 들어오면 1이 되기 때문이다.
    """
    records = [
        annual("한미반도체", 2024),
        annual("한미반도체", "2026"),
        annual("한미반도체", None),
        annual("한미반도체", True),
    ]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2024


def test_anchor_map_is_keyed_by_normalized_company_name() -> None:
    """대응표의 키는 질의 해석 계층이 쓰는 정규화 표기와 같아야 한다."""
    anchors = latest_annual_fiscal_year_by_company(
        [annual("한미반도체(주)", 2025), annual("삼성전자", 2024)]
    )

    assert anchors == {"한미반도체": 2025, "삼성전자": 2024}


# --------------------------------------------------------------------------
# 2. 기준 연도는 지표 가용성과 실행 시각을 보지 않는다
# --------------------------------------------------------------------------


def test_anchor_ignores_metric_availability() -> None:
    """
    요청 지표를 읽을 수 있는지로 기준 연도를 바꾸지 않는다.

    구조로 막는다. 이 함수가 받는 것은 manifest 레코드와 기업명뿐이며, 지표를 받을 자리가
    없으므로 지표 가용성이 결과에 영향을 줄 수 없다.

    인자 이름을 검사하는 이유는, 나중에 metric 인자가 조용히 추가되면 그 순간 fallback 경로가
    열리기 때문이다. 그때 이 테스트가 먼저 깨져야 한다.
    """
    assert set(inspect.signature(latest_annual_fiscal_year).parameters) == {
        "records",
        "company",
    }

    forbidden = {"metric", "metrics", "fact", "value", "extractor", "corpus_root"}

    assert not (
        set(inspect.signature(latest_annual_fiscal_year_by_company).parameters) & forbidden
    )


def test_anchor_holds_when_the_newest_filing_has_no_readable_content() -> None:
    """
    가장 최신 사업보고서에서 값을 읽지 못해도 기준 연도는 그대로다.

    manifest는 문서의 존재만 말하고 그 안에 무엇이 있는지는 말하지 않는다. 기준 연도가
    manifest만 보고 정해진다는 사실이 곧 '추출 실패로 앞당겨지지 않는다'는 보장이다.

    아래 레코드에서 FY2025 문서는 파일이 없는 것으로 표시돼 있다. 그래도 anchor는 2025다.
    실제 실행에서 그 해의 값을 찾지 못하면 filing_discovery나 extraction이 NOT_FOUND로
    답하며, 그 판정은 질문이 물은 기간을 바꾸지 않는다.
    """
    records = [
        annual("한미반도체", 2024),
        annual("한미반도체", 2025, n_files=0, file_format=None),
    ]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2025

    plan = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=latest_annual_fiscal_year_by_company(records),
    )

    assert plan.years == (2024, 2025)


@pytest.mark.parametrize(
    "module_path",
    [
        PACKAGE_ROOT / "agent" / "query_analyzer.py",
        PACKAGE_ROOT / "retrieval" / "metadata_filter.py",
    ],
    ids=lambda path: path.name,
)
def test_relative_period_resolution_does_not_read_the_clock(module_path: Path) -> None:
    """
    해석 경로에 시각을 읽는 import가 없다.

    "오늘이 2026년이니 작년은 2025"는 그럴듯하지만, 그렇게 정한 값은 해가 바뀌는 순간 조용히
    달라진다. 평가에서 어제 맞던 답이 오늘 틀리고, 그 원인은 결과만 봐서는 보이지 않는다.
    값을 검사하는 대신 시각을 읽을 수단 자체가 없는지 본다.
    """
    tree = ast.parse(module_path.read_text(encoding="utf-8"), filename=str(module_path))

    imported: set[str] = set()

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name.split(".")[0] for alias in node.names)

        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            imported.add(node.module.split(".")[0])

    assert not (imported & {"datetime", "time", "calendar"})


def test_anchor_does_not_depend_on_the_current_year() -> None:
    """
    같은 레코드는 언제 실행해도 같은 기준 연도를 준다.

    위 import 검사가 구조를 막고, 이 케이스는 값을 고정한다. 현재 연도가 2026이든 2030이든
    아래 레코드의 anchor는 2025다.
    """
    records = [annual("한미반도체", 2024), annual("한미반도체", 2025)]

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2025


def test_metadata_filter_exposes_no_clock_backed_default() -> None:
    """모듈이 '현재 연도' 같은 기본값을 들고 있지 않은지 본다."""
    suspicious = [
        name
        for name in dir(metadata_filter)
        if not name.startswith("_") and "current_year" in name.lower()
    ]

    assert suspicious == []


# --------------------------------------------------------------------------
# 3. 지원하는 상대 기간 표현
# --------------------------------------------------------------------------


@pytest.mark.parametrize("expression", PREVIOUS_YEAR_EXPRESSIONS)
def test_supported_expressions_resolve_to_anchor_and_anchor_minus_one(
    expression: str,
) -> None:
    """지원 표현 넷은 모두 anchor - 1과 anchor를 견주는 계획이 된다."""
    plan = build_query_plan(
        f"한미반도체 매출이 {expression}보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    assert plan.years == (2024, 2025)
    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.relative_period is not None
    assert plan.relative_period.expression == expression
    assert plan.relative_period.anchor_year == 2025
    assert plan.relative_period.anchor_source == ANCHOR_SOURCE_LATEST_ANNUAL_FILING
    assert plan.relative_period.resolved is True
    assert plan.has_unresolved_relative_period is False


def test_resolved_years_follow_the_anchor() -> None:
    """기준 연도가 다르면 옮겨지는 두 해도 함께 움직인다."""
    plan = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors={"한미반도체": 2024},
    )

    assert plan.years == (2023, 2024)


def test_resolved_years_are_ordered_from_old_to_new() -> None:
    """
    years 순서는 오래된 것부터다.

    뒤집으면 같은 변화에 부호가 뒤집힌 값이 나오고, 뒤집힌 값도 그럴듯하다. 순서를 계획
    쪽에서 고정해 두면 실행하는 쪽이 매번 다시 정하지 않는다.
    """
    plan = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    assert plan.years == tuple(sorted(plan.years))
    assert plan.years[0] == plan.years[1] - 1


def test_relative_period_offset_is_backward_only() -> None:
    """미래 방향 오프셋은 만들 수 없다."""
    assert PREVIOUS_YEAR_OFFSET == -1

    with pytest.raises(ValueError):
        RelativePeriod(expression="내년", offset=1)


def test_relative_period_requires_anchor_year_and_source_together() -> None:
    """연도만 있고 출처가 없으면 그 값이 무엇을 보고 정해졌는지 알 수 없다."""
    with pytest.raises(ValueError):
        RelativePeriod(expression="작년", offset=-1, anchor_year=2025)

    with pytest.raises(ValueError):
        RelativePeriod(
            expression="작년",
            offset=-1,
            anchor_source=ANCHOR_SOURCE_LATEST_ANNUAL_FILING,
        )


# --------------------------------------------------------------------------
# 4. false positive — 두 해 앞 표현이 한 해 앞으로 해석되지 않는다
# --------------------------------------------------------------------------


@pytest.mark.parametrize("expression", ["재작년", "지지난해", "전전년", "전전년도"])
def test_two_years_back_expressions_are_not_read_as_one_year_back(
    expression: str,
) -> None:
    """
    "재작년"은 "작년"을 안에 담고 있다. 부분문자열 검사로 보면 걸리고, 걸린 것을 그대로
    해석하면 두 해 앞을 물은 질문에 한 해 앞을 답하게 된다.

    근거를 갖춘 채로 질문과 다른 기간을 답하는 형태이며, 개수나 형식으로는 드러나지 않는다.
    그래서 조용히 옮기지 않고 거절한다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            f"한미반도체 매출이 {expression}보다 얼마나 늘었어?",
            known_companies=KNOWN_COMPANIES,
            fiscal_year_anchors=ANCHORS,
        )

    assert error.value.reason == "unsupported_relative_period"

    # 어느 표현에 걸렸는지가 사유에 남아야 무엇을 구현하면 풀리는지 알 수 있다.
    assert expression in error.value.detail


@pytest.mark.parametrize("expression", ["전년 동기", "작년 동기", "지난해 동기"])
def test_same_quarter_last_year_expressions_are_fail_closed(expression: str) -> None:
    """
    "전년 동기"가 가리키는 것은 전년도 연간이 아니라 전년 같은 분기다.

    지원 패턴에는 "전년"이 그대로 걸리므로, 따로 막지 않으면 분기 값을 물은 질문에 연간 값을
    답하게 된다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            f"한미반도체 {expression} 매출이 얼마야?",
            known_companies=KNOWN_COMPANIES,
            fiscal_year_anchors=ANCHORS,
        )

    assert error.value.reason == "unsupported_relative_period"


def test_electric_wording_is_not_treated_as_a_relative_period() -> None:
    """
    '전기'는 상대 기간 어휘에 넣지 않는다.

    회계 용어로는 전년도를 뜻하지만 "전기차"·"전기전자"가 같은 두 글자로 시작하고, 그쪽이 이
    코퍼스에서 훨씬 흔한 기재 사실이다. 낱말 하나로 막으면 답할 수 있는 조회 질문을 막는다.
    """
    plan = build_query_plan(
        "한미반도체 2025년 전기차 부품 매출액이 얼마야?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    assert plan.relative_period is None
    assert plan.years == (2025,)


# --------------------------------------------------------------------------
# 5. 미래 방향은 이 해석기가 다루지 않는다 (Pattern F 소관)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "question",
    [
        "한미반도체 내년 매출 알려줘",
        "한미반도체 다음 해 매출 알려줘",
        "한미반도체 앞으로 매출이 얼마나 늘어?",
        "한미반도체 내후년 매출 알려줘",
        "한미반도체 차기 매출 알려줘",
    ],
)
def test_future_relative_period_never_becomes_a_historical_query(question: str) -> None:
    """
    미래 상대 기간은 절대연도로 옮겨지지 않는다.

    옮기면 아직 존재할 수 없는 문서를 찾는 질의가 되고, 검색 실패(NOT_FOUND)로 답하게 된다.
    그것은 '보유 기간 밖이라 있을 수 없다'(F1)와 다른 진술이다. 미래 방향 판정은 Pattern F가
    맡으며, 이 해석기는 만들지 않는 쪽으로 남는다.

    지금은 연도를 하나도 읽지 못해 막힌다. 상대 기간 해석 경로가 열린 뒤에도 그대로여야 한다.
    """
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            question,
            known_companies=KNOWN_COMPANIES,
            fiscal_year_anchors=ANCHORS,
        )

    assert error.value.reason == "missing_year"


def test_future_expression_is_absent_from_the_supported_vocabulary() -> None:
    """지원 어휘에 미래 방향 표현이 섞여 들어가지 않았는지 본다."""
    future = ("내년", "다음", "차기", "향후", "앞으로", "장래", "올해", "금년")

    for expression in PREVIOUS_YEAR_EXPRESSIONS:
        assert not any(token in expression for token in future)


# --------------------------------------------------------------------------
# 6. 기준 연도가 없으면 막는다
# --------------------------------------------------------------------------


def test_missing_anchor_table_is_fail_closed() -> None:
    """대응표를 주지 않으면 오늘 연도로 대신하지 않고 막는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "한미반도체 매출이 작년보다 얼마나 늘었어?",
            known_companies=KNOWN_COMPANIES,
        )

    assert error.value.reason == "missing_fiscal_year_anchor"


def test_company_without_annual_filing_is_fail_closed() -> None:
    """대응표에 그 기업이 없으면 다른 기업의 기준 연도를 빌려 쓰지 않는다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(
            "삼성전자 매출이 작년보다 얼마나 늘었어?",
            known_companies=KNOWN_COMPANIES,
            fiscal_year_anchors={"한미반도체": 2025},
        )

    assert error.value.reason == "missing_fiscal_year_anchor"
    assert "삼성전자" in error.value.detail


# --------------------------------------------------------------------------
# 7. 절대연도와 상대 기간이 함께 쓰인 질문은 옮기지 않는다
# --------------------------------------------------------------------------


def test_absolute_year_with_relative_period_stays_unresolved() -> None:
    """
    "2025년 매출이 전년보다"에서 "전년"의 기준은 정해지지 않았다.

    문장에 적힌 2025일 수도, 기업의 최신 사업보고서 연도일 수도 있다. D8이 정한 것은 기준을
    밖에서 가져와야 하는 경우 하나뿐이고, 여기는 문장 안에 후보가 이미 있다. 둘 중 하나를 골라
    옮기면 고른 쪽이 조용히 답이 된다.

    읽었다는 사실은 계획에 남는다. 그래야 실행하는 쪽이 years만으로 답하지 않는다.
    """
    plan = build_query_plan(
        "한미반도체 2025년 매출이 전년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.years == (2025,)
    assert plan.relative_period is not None
    assert plan.relative_period.expression == "전년"
    assert plan.relative_period.anchor_year is None
    assert plan.has_unresolved_relative_period is True


def test_unresolved_state_is_readable_without_reparsing_the_question() -> None:
    """
    해석 여부는 계획 구조가 답한다.

    질문 문자열을 다시 읽어 판정하면, 해석에 성공한 계획도 "작년"이라는 글자가 남아 있다는
    이유로 막힌다. 답을 축소하지 않으려고 만든 판정이 답할 수 있는 질문을 막는 쪽으로 틀린다.
    """
    resolved = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    unresolved = build_query_plan(
        "한미반도체 2025년 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    # 두 질문 모두 "작년"을 담고 있다. 문자열로는 갈리지 않는다.
    assert "작년" in resolved.question
    assert "작년" in unresolved.question

    assert resolved.has_unresolved_relative_period is False
    assert unresolved.has_unresolved_relative_period is True


# --------------------------------------------------------------------------
# 8. Q07 대표 질문
# --------------------------------------------------------------------------


def test_q07_representative_question_plan() -> None:
    """
    대표 질문 Q07이 만드는 계획을 통째로 고정한다.

    이번 PR의 성공 기준은 Q07이 ANSWERABLE이 되는 것이 아니다. 상대 기간이 결정적으로 정확한
    두 연도로 옮겨지는 것까지이며, 증감 계산은 다음 PR 범위다.
    """
    plan = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    assert plan.companies == ("한미반도체",)
    assert plan.years == (2024, 2025)
    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.metric == METRIC_REVENUE
    assert plan.derived_metric is None
    assert plan.document_class == "periodic"
    assert plan.document_subtype == "annual"


def test_q07_warning_explains_the_anchor() -> None:
    """
    경고 문구만 읽고도 해석 근거를 되짚을 수 있어야 한다.

    남아야 하는 것: 어떤 상대 표현을, 어느 기준 연도로, 어느 두 해로 옮겼는지와, 그 기준
    연도를 무엇에서 정했는지.
    """
    plan = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    assert len(plan.warnings) == 1

    warning = plan.warnings[0]

    assert "작년" in warning
    assert "2025" in warning
    assert "2024" in warning
    assert "사업보고서" in warning
    assert "실행 시각" in warning


def test_q07_plan_is_serializable() -> None:
    """계획을 진단용으로 내보낼 때 상대 기간 상태가 함께 실린다."""
    plan = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=KNOWN_COMPANIES,
        fiscal_year_anchors=ANCHORS,
    )

    payload = plan.to_dict()

    assert payload["years"] == (2024, 2025)
    assert payload["relative_period"]["anchor_year"] == 2025
    assert payload["relative_period"]["anchor_source"] == ANCHOR_SOURCE_LATEST_ANNUAL_FILING


# --------------------------------------------------------------------------
# 9. 절대연도를 적은 대표 질문은 이 변경에 영향을 받지 않는다
# --------------------------------------------------------------------------

REGRESSION_COMPANIES = (
    "한미반도체",
    "삼성전자",
    "삼성바이오로직스",
    "HD현대중공업",
    "삼성중공업",
)

REGRESSION_ANCHORS = {
    "한미반도체": 2025,
    "삼성전자": 2025,
    "삼성바이오로직스": 2025,
    "hd현대중공업": 2025,
    "삼성중공업": 2025,
}

ABSOLUTE_PERIOD_QUESTIONS = (
    # Q01
    "한미반도체 2025년 연결 기준 매출액이 얼마야?",
    # Q08
    "한미반도체 영업이익률이 2024년이랑 2025년에 어떻게 달라졌어?",
    # Q09
    "삼성바이오로직스 사업 구조가 2023년이랑 2025년이랑 어떻게 달라졌어?",
    # Q10
    "2025년 HD현대중공업이랑 삼성중공업 매출 비교해줘.",
    # Q11
    "HD현대중공업이랑 삼성중공업 중에 2025년 영업이익률이 더 높은 곳은 어디야?",
)


@pytest.mark.parametrize("question", ABSOLUTE_PERIOD_QUESTIONS)
def test_anchor_table_does_not_change_absolute_period_plans(question: str) -> None:
    """
    기준 연도 대응표를 줘도 절대연도 질문의 계획은 그대로다.

    대응표는 상대 기간을 옮길 때만 읽힌다. 그 사실을 계획 전체를 견줘서 고정한다. 필드 몇
    개만 보면 나중에 다른 필드가 조용히 달라져도 통과한다.
    """
    without_anchors = build_query_plan(question, known_companies=REGRESSION_COMPANIES)

    with_anchors = build_query_plan(
        question,
        known_companies=REGRESSION_COMPANIES,
        fiscal_year_anchors=REGRESSION_ANCHORS,
    )

    assert without_anchors == with_anchors

    # 상대 기간 표현이 없는 질문에는 그 상태가 붙지 않는다.
    assert with_anchors.relative_period is None
    assert with_anchors.has_unresolved_relative_period is False


@pytest.mark.parametrize("question", ABSOLUTE_PERIOD_QUESTIONS)
def test_absolute_period_questions_keep_their_fiscal_years(question: str) -> None:
    """질문에 적힌 연도가 기준 연도로 대체되지 않는다."""
    plan = build_query_plan(
        question,
        known_companies=REGRESSION_COMPANIES,
        fiscal_year_anchors=REGRESSION_ANCHORS,
    )

    for year in plan.years:
        assert f"{year}년" in question


# --------------------------------------------------------------------------
# 10. 실측 코퍼스 대조
#
# 위 케이스들은 합성 레코드로 규칙을 고정한다. 이 절은 그 규칙이 실제 manifest에서도 같은
# 값을 내는지 본다. 코퍼스가 없으면 건너뛴다.
# --------------------------------------------------------------------------

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)


def _resolve_manifest_path() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    manifest = candidate / "manifest.jsonl"

    return manifest if manifest.is_file() else None


@pytest.mark.corpus
def test_actual_corpus_anchor_for_the_q07_company() -> None:
    """
    한미반도체의 기준 연도가 실측 manifest에서도 FY2025인지 확인한다.

    Q07 gold의 FY2024·FY2025는 이 값에서 나온다(open_decisions.md D8). 코퍼스에 FY2026
    사업보고서가 추가되면 이 테스트가 먼저 깨지고, 그때 gold를 함께 옮기면 된다.
    """
    manifest_path = _resolve_manifest_path()

    if manifest_path is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    records = load_manifest(manifest_path)

    assert latest_annual_fiscal_year(records, company="한미반도체") == 2025


@pytest.mark.corpus
def test_actual_corpus_q07_plan_resolves_to_two_fiscal_years() -> None:
    """실측 manifest로 만든 Q07 계획이 FY2024·FY2025를 견주는지 확인한다."""
    manifest_path = _resolve_manifest_path()

    if manifest_path is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    records = load_manifest(manifest_path)

    plan = build_query_plan(
        "한미반도체 매출이 작년보다 얼마나 늘었어?",
        known_companies=list_company_names(records),
        fiscal_year_anchors=latest_annual_fiscal_year_by_company(records),
    )

    assert plan.years == (2024, 2025)
    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.metric == METRIC_REVENUE
    assert plan.has_unresolved_relative_period is False
