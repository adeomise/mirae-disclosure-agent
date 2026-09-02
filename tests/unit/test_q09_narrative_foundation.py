"""Q09 서술 비교의 기반 계층 단위 테스트.

검증 대상은 셋이다. 어느 것도 handler를 부르지 않는다.

    1. 공유 Contract   NarrativeComparisonResult / NarrativeComparisonClaim
    2. 질의 해석       Q09가 business_overview 범위를 담은 시계열 비교 계획이 되는가
    3. Section 규칙    business_overview가 사업보고서에서 Section 하나로 확정되는가

이 슬라이스가 고정하는 것은 "무엇을 견줄 수 있는 상태인가"까지다. 실제로 견주는 일
(agent.handlers.temporal_compare)과 그 결과를 문장으로 옮기는 일(agent.answer_builder)은
아직 없으며, 그 두 모듈은 이 변경에서 건드리지 않는다.

코퍼스는 열지 않는다. 마지막 절만 예외이며, 실제 코퍼스가 있을 때에만 Section 규칙이 실제
서식에서도 하나로 확정되는지 확인한다. 코퍼스를 고치지는 않는다.
"""

from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError

from disclosure_agent.agent.query_analyzer import (
    INTENT_TEMPORAL_COMPARE,
    build_query_plan,
)
from disclosure_agent.common.enums import ExecutionStatus, SectionScope
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    NarrativeComparisonClaim,
    NarrativeComparisonResult,
)
from disclosure_agent.periodic.sections import (
    REASON_AMBIGUOUS_SECTION,
    REASON_NO_MATCHING_SECTION,
    SectionNavigationError,
    select_scope_section,
)

# 대표 질문 원문. evaluation/questions.jsonl의 표기와 같아야 한다.
Q09 = "삼성바이오로직스 사업 구조가 2023년이랑 2025년이랑 어떻게 달라졌어?"

Q09_COMPANY = "삼성바이오로직스"

KNOWN_COMPANIES = ("삼성바이오로직스", "테스트반도체(주)", "한미반도체")

# 표준 서식에서 사업 구조를 서술하는 자리.
BUSINESS_OVERVIEW_SECTION_ID = "section_0009"
BUSINESS_OVERVIEW_SECTION_PATH = ["II. 사업의 내용", "1. 사업의 개요"]

FACILITY_SECTION_ID = "section_0003"

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
CORPUS_MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

# 실제 코퍼스 확인에 쓰는 회계연도. Q09가 견주는 두 해다.
CORPUS_FISCAL_YEARS = (2023, 2025)


def _claim(**overrides: Any) -> dict[str, Any]:
    """변화 항목 하나의 최소 필드."""
    fields: dict[str, Any] = {
        "claim_id": "claim_business_overview_0001",
        "label": "바이오의약품 개발 및 상업화 부문의 구성 변화",
        "left_evidence_ids": ["evidence_periodic_2023_section_0009_p0"],
        "right_evidence_ids": ["evidence_periodic_2025_section_0009_p0"],
    }

    fields.update(overrides)

    return fields


def _result(**overrides: Any) -> dict[str, Any]:
    """서술 비교 결과 하나의 최소 필드."""
    fields: dict[str, Any] = {
        "comparison_id": "narrative_comparison_business_overview_2023_2025",
        "company": Q09_COMPANY,
        "section_scope": SectionScope.BUSINESS_OVERVIEW,
        "left_fiscal_year": 2023,
        "right_fiscal_year": 2025,
        "left_section_path": BUSINESS_OVERVIEW_SECTION_PATH,
        "right_section_path": BUSINESS_OVERVIEW_SECTION_PATH,
    }

    fields.update(overrides)

    return fields


def _with_business_overview(document: dict[str, Any]) -> dict[str, Any]:
    """합성 사업보고서에 '1. 사업의 개요' Section을 더한 사본."""
    copied = copy.deepcopy(document)

    copied["sections"].append(
        {
            "section_id": BUSINESS_OVERVIEW_SECTION_ID,
            "title": "1. 사업의 개요",
            "level": 2,
            "parent_section_id": "section_0001",
            "section_path": list(BUSINESS_OVERVIEW_SECTION_PATH),
            "paragraphs": ["가. 사업개요 당사가 영위하는 사업은 두 부문으로 나누어져 있습니다."],
            "text": "가. 사업개요 당사가 영위하는 사업은 두 부문으로 나누어져 있습니다.",
        }
    )

    return copied


# --------------------------------------------------------------------------
# 1. 공유 Contract
# --------------------------------------------------------------------------


def test_narrative_comparison_result_round_trips() -> None:
    """직렬화하고 다시 읽어도 같은 결과다."""
    result = NarrativeComparisonResult(
        **_result(claims=[NarrativeComparisonClaim(**_claim())])
    )

    restored = NarrativeComparisonResult.model_validate(result.model_dump())

    assert restored == result
    assert restored.section_scope is SectionScope.BUSINESS_OVERVIEW
    assert [claim.claim_id for claim in restored.claims] == [
        "claim_business_overview_0001"
    ]


def test_claim_without_left_evidence_is_rejected() -> None:
    """
    한쪽 연도의 근거만으로는 변화 항목이 되지 않는다.

    비교 대상 연도의 근거가 없으면 그 항목은 "달라졌다"가 아니라 "한 해에 이렇게 적혀 있다"
    까지만 말할 수 있다. 통과시키면 한 해의 서술을 변화로 옮겨 적은 항목이 남는다.
    """
    with pytest.raises(ValidationError):
        NarrativeComparisonClaim(**_claim(left_evidence_ids=[]))


def test_claim_without_right_evidence_is_rejected() -> None:
    """반대쪽도 같다. 어느 쪽이 비었든 변화 항목이 성립하지 않는다."""
    with pytest.raises(ValidationError):
        NarrativeComparisonClaim(**_claim(right_evidence_ids=[]))


def test_claim_requires_a_label() -> None:
    """이름 없는 변화 항목은 무엇이 달라졌는지 말하지 않는다."""
    with pytest.raises(ValidationError):
        NarrativeComparisonClaim(**_claim(label=""))


@pytest.mark.parametrize(
    ("left", "right"),
    [
        # 같은 해를 자기 자신과 견준 결과는 변화가 없다는 판정처럼 보이지만, 실제로는
        # 비교를 하지 않은 것이다.
        (2023, 2023),
        # 질문에 적힌 순서를 그대로 쓰면 같은 변화가 방향이 뒤집힌 채로 서술된다.
        (2025, 2023),
    ],
)
def test_fiscal_years_must_be_distinct_and_ordered(left: int, right: int) -> None:
    with pytest.raises(ValidationError):
        NarrativeComparisonResult(
            **_result(left_fiscal_year=left, right_fiscal_year=right)
        )


def test_no_material_change_keeps_an_empty_claim_list() -> None:
    """
    변화를 찾지 못한 결과도 결과다.

    claims에 최소 길이를 두지 않는 이유이며, 이것이 '변화 없음'과 '보지 못함'을 나누는
    자리다. 후자는 이 결과를 만들지 않고 상위 단계가 막는다.
    """
    result = NarrativeComparisonResult(**_result())

    assert result.claims == []
    assert result.left_section_path and result.right_section_path


def test_section_paths_are_required_on_both_sides() -> None:
    """같은 의미 범위를 견줬다는 사실은 결과에 남아야 확인할 수 있다."""
    for field in ("left_section_path", "right_section_path"):
        with pytest.raises(ValidationError):
            NarrativeComparisonResult(**_result(**{field: []}))


def test_result_does_not_carry_cause_or_numeric_linkage() -> None:
    """
    원인도 수치 참조도 담지 않는다.

    담을 자리를 만들어 두면 비워 두기로 한 판단이 다음 구현에서 조용히 채워진다. Q09는
    계산 경로를 지나지 않으므로(agent_patterns.md Pattern C 서술 경로) 그 자리가 없어야 한다.
    """
    fields = set(NarrativeComparisonResult.model_fields) | set(
        NarrativeComparisonClaim.model_fields
    )

    forbidden = {
        "cause",
        "causes",
        "reason",
        "explanation",
        "calculation_id",
        "calculation_ids",
        "comparison_ids",
        "inputs",
        "value",
        "unit",
        "relation",
    }

    assert fields & forbidden == set()


# --------------------------------------------------------------------------
# 2. 공통 실행 Contract에 붙는 자리
# --------------------------------------------------------------------------


def test_execution_result_defaults_to_independent_empty_lists() -> None:
    """
    기본값이 인스턴스마다 따로여야 한다.

    하나를 공유하면 한 실행 결과에 담은 서술 비교가 다른 실행 결과에도 나타난다.
    """
    first = AgentExecutionResult(execution_status=ExecutionStatus.COMPLETED)
    second = AgentExecutionResult(execution_status=ExecutionStatus.COMPLETED)

    assert first.narrative_comparisons == []
    assert second.narrative_comparisons == []
    assert first.narrative_comparisons is not second.narrative_comparisons

    first.narrative_comparisons.append(NarrativeComparisonResult(**_result()))

    assert second.narrative_comparisons == []


def test_narrative_comparisons_are_separate_from_numeric_results() -> None:
    """
    수치 결과와 한 목록에 담지 않는다.

    ComparisonResult는 두 값의 우열이고 이쪽은 두 서술의 변화 항목이다. 섞으면 받는 쪽이
    어느 것이 수치 판정인지 항목마다 확인해야 한다.
    """
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        narrative_comparisons=[NarrativeComparisonResult(**_result())],
    )

    assert len(result.narrative_comparisons) == 1
    assert result.comparisons == []
    assert result.calculations == []


def test_public_answer_response_is_unchanged_in_this_slice() -> None:
    """
    공개 응답 Contract는 이 슬라이스에서 넓히지 않는다.

    서술 비교를 공개 응답에 어떻게 싣는지는 문장 조립과 함께 정할 사항이고, 그 모듈은 아직
    없다. 자리를 먼저 만들면 채우지 않은 필드가 응답에 나간다.
    """
    from disclosure_agent.common.schemas import AnswerResponse

    assert "narrative_comparisons" not in AnswerResponse.model_fields


# --------------------------------------------------------------------------
# 3. 질의 해석
# --------------------------------------------------------------------------


def test_q09_resolves_to_a_business_overview_temporal_plan() -> None:
    """대표 질문 Q09의 계획을 축마다 고정한다."""
    plan = build_query_plan(Q09, known_companies=KNOWN_COMPANIES)

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.companies == (Q09_COMPANY,)
    assert plan.years == (2023, 2025)
    assert plan.section_scope is SectionScope.BUSINESS_OVERVIEW
    assert plan.metric is None
    assert plan.derived_metric is None


@pytest.mark.parametrize(
    "question",
    [
        "삼성바이오로직스 사업 구조가 2023년이랑 2025년이랑 어떻게 달라졌어?",
        "삼성바이오로직스 사업구조가 2023년이랑 2025년이랑 어떻게 달라졌어?",
        "삼성바이오로직스 사업 개요가 2023년이랑 2025년이랑 어떻게 달라졌어?",
        "삼성바이오로직스 사업의 개요가 2023년이랑 2025년이랑 어떻게 달라졌어?",
    ],
)
def test_business_overview_tokens_survive_spacing_variants(question: str) -> None:
    """표기가 달라도 같은 범위를 가리킨다. 공백을 걷어내고 보기 때문이다."""
    plan = build_query_plan(question, known_companies=KNOWN_COMPANIES)

    assert plan.section_scope is SectionScope.BUSINESS_OVERVIEW


@pytest.mark.parametrize(
    "question",
    [
        # 지표를 읽지 못했지만 범위를 밝히지도 않은 질문. 아직 어휘가 없는 원천 지표를
        # 물었을 수 있으므로 서술 비교로 단정하지 않는다.
        "테스트반도체(주) 2023년이랑 2025년이랑 어떻게 달라졌어?",
        # 원천 지표의 시계열 증감. Pattern C의 수치 경로가 다룰 질문이다.
        "테스트반도체(주) 2024년 매출과 2025년 매출 비교해줘",
    ],
)
def test_metric_less_temporal_question_without_a_scope_token_is_not_narrative(
    question: str,
) -> None:
    """
    지표가 비어 있다는 것만으로 서술 비교가 되지 않는다.

    서술 경로와 수치 경로를 가르는 것은 '어느 범위를 견주라고 했는가'이다. 지표의 부재로
    가르면, 어휘가 없는 원천 지표를 물은 질문이 서술 경로로 끌려가 엉뚱한 Section을 견준
    답이 나온다.
    """
    plan = build_query_plan(question, known_companies=KNOWN_COMPANIES)

    assert plan.intent == INTENT_TEMPORAL_COMPARE
    assert plan.section_scope is None


def test_facility_investment_classification_is_unchanged() -> None:
    """
    설비 투자 범위의 판정은 그대로다. 어휘를 넓히면서 기존 범위를 가로채지 않는다.

    Q04 질문에는 '사업보고서'가 들어 있다. business_overview 표기를 '사업'으로 두면 이
    질문이 두 범위를 함께 가리키게 되어 ambiguous_section_scope로 막힌다.
    """
    plan = build_query_plan(
        "삼성바이오로직스 2025년 사업보고서에서 설비 투자 관련 내용 정리해줘.",
        known_companies=KNOWN_COMPANIES,
    )

    assert plan.section_scope is SectionScope.FACILITY_INVESTMENT


# --------------------------------------------------------------------------
# 4. Section 규칙
# --------------------------------------------------------------------------


def test_business_overview_scope_selects_exactly_one_section(
    summary_document: dict[str, Any],
) -> None:
    document = _with_business_overview(summary_document)

    selection = select_scope_section(
        document,
        section_scope=SectionScope.BUSINESS_OVERVIEW,
    )

    assert selection.section_id == BUSINESS_OVERVIEW_SECTION_ID
    assert selection.section_path == tuple(BUSINESS_OVERVIEW_SECTION_PATH)


def test_business_overview_without_the_required_ancestor_is_not_selected(
    summary_document: dict[str, Any],
) -> None:
    """
    상위 Section 조건이 없으면 같은 낱말을 담은 다른 Section이 함께 걸린다.

    '개요'는 문서 여러 곳에 나온다. 규칙을 느슨하게 두면 회사의 개요를 사업 구조로 읽는다.
    """
    document = _with_business_overview(summary_document)

    for section in document["sections"]:
        if section["section_id"] == BUSINESS_OVERVIEW_SECTION_ID:
            section["section_path"] = ["I. 회사의 개요", "1. 사업의 개요"]

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.BUSINESS_OVERVIEW)

    assert error.value.reason == REASON_NO_MATCHING_SECTION


def test_missing_business_overview_does_not_fall_back(
    summary_document: dict[str, Any],
) -> None:
    """범위를 찾지 못하면 상위 Section이나 문서 전체로 넓히지 않는다."""
    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(
            summary_document,
            section_scope=SectionScope.BUSINESS_OVERVIEW,
        )

    assert error.value.reason == REASON_NO_MATCHING_SECTION


def test_multiple_business_overview_sections_are_not_narrowed_by_order(
    summary_document: dict[str, Any],
) -> None:
    """후보가 여럿이면 첫 매치를 고르지 않는다."""
    document = _with_business_overview(summary_document)

    duplicate = copy.deepcopy(
        next(
            section
            for section in document["sections"]
            if section["section_id"] == BUSINESS_OVERVIEW_SECTION_ID
        )
    )
    duplicate["section_id"] = "section_0098"
    duplicate["section_path"] = ["II. 사업의 내용", "8. 사업의 개요 보충"]

    document["sections"].append(duplicate)

    with pytest.raises(SectionNavigationError) as error:
        select_scope_section(document, section_scope=SectionScope.BUSINESS_OVERVIEW)

    assert error.value.reason == REASON_AMBIGUOUS_SECTION


def test_business_overview_rule_does_not_capture_the_facility_section(
    summary_document: dict[str, Any],
) -> None:
    """두 범위가 서로를 끌어오지 않는다. 같은 상위 Section 아래 나란히 있기 때문이다."""
    document = _with_business_overview(summary_document)

    facility = select_scope_section(
        document,
        section_scope=SectionScope.FACILITY_INVESTMENT,
    )

    overview = select_scope_section(
        document,
        section_scope=SectionScope.BUSINESS_OVERVIEW,
    )

    assert facility.section_id == FACILITY_SECTION_ID
    assert overview.section_id == BUSINESS_OVERVIEW_SECTION_ID


# --------------------------------------------------------------------------
# 5. 실제 코퍼스 확인
#
# 합성 문서는 규칙이 무엇을 고르는지 고정하고, 여기서는 그 규칙이 실제 서식에서도 하나로
# 확정되는지만 본다. 코퍼스를 고치지 않으며, 값도 원문도 이 파일에 적지 않는다.
# --------------------------------------------------------------------------


def _require_actual_corpus() -> tuple[Path, Path]:
    raw_corpus_root = os.environ.get(CORPUS_ROOT_ENV)

    if not raw_corpus_root:
        pytest.skip(f"{CORPUS_ROOT_ENV} is not set")

    corpus_root = Path(raw_corpus_root).expanduser()

    if not corpus_root.is_dir():
        pytest.skip(f"corpus root is not a directory: {corpus_root}")

    raw_manifest = os.environ.get(CORPUS_MANIFEST_ENV)

    manifest_path = (
        Path(raw_manifest).expanduser()
        if raw_manifest is not None
        else corpus_root / "manifest.jsonl"
    )

    if not manifest_path.is_file():
        pytest.skip(f"corpus manifest does not exist: {manifest_path}")

    return corpus_root, manifest_path


@pytest.mark.corpus
@pytest.mark.parametrize("fiscal_year", CORPUS_FISCAL_YEARS)
def test_business_overview_resolves_one_section_in_the_actual_corpus(
    fiscal_year: int,
) -> None:
    """
    Q09가 견주는 두 해의 사업보고서에서 범위가 각각 Section 하나로 확정된다.

    양쪽 모두 확인하는 것이 요점이다. 한쪽만 확정되면 견줄 수 없고, 그 경우 실행 단계는
    막혀야 한다. 규칙이 실제 서식에서 성립하는지를 먼저 확인해 두면 그 정지가 규칙의 문제인지
    문서의 문제인지 가릴 수 있다.
    """
    from disclosure_agent.periodic.service import load_parsed_document
    from disclosure_agent.retrieval.metadata_filter import list_filings

    corpus_root, manifest_path = _require_actual_corpus()

    filings = list_filings(
        manifest_path=manifest_path,
        corpus_root=corpus_root,
        company=Q09_COMPANY,
        document_class="periodic",
        document_subtype="annual",
        year=fiscal_year,
    )

    if len(filings) != 1:
        pytest.skip(
            f"{Q09_COMPANY} {fiscal_year} 사업보고서가 1건이 아닙니다: {len(filings)}건"
        )

    document = load_parsed_document(filings[0].main_document)

    selection = select_scope_section(
        document,
        section_scope=SectionScope.BUSINESS_OVERVIEW,
    )

    assert selection.section_path[-1].endswith("사업의 개요")
    assert any("사업의 내용" in ancestor for ancestor in selection.section_path[:-1])
