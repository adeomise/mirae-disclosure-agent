"""Pattern C 서술 경로(Q09) 통합 테스트.

이번 구현이 고정하는 것:
- 범위를 밝힌 시계열 질문이 서술 경로로 들어간다
- 두 연도의 같은 의미 Section을 모두 확보하고 Evidence를 양쪽 다 남긴다
- 항목을 짝지을 수 없으면 COMPLETED / UNANSWERABLE / NOT_COMPARABLE로 답한다
- 값을 하나도 만들지 않는다

이번 구현이 하지 않는 것:
- 변화 항목(NarrativeComparisonClaim)을 만들지 않는다. 짝짓기 규칙이 없기 때문이며,
  그래서 '변화 없음'으로도 답하지 않는다(handlers/temporal_compare의 서술 경로 주석).

실제 코퍼스를 열지 않는다. 공시 선택은 합성 manifest가, 문서 내용은 합성 parsed document가
맡는다. 실제 코퍼스 확인은 tests/integration/test_api_q09_corpus.py가 따로 한다.
"""

from __future__ import annotations

import copy
import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import router
from disclosure_agent.agent.answer_builder import (
    REASON_AMBIGUOUS_FILING,
    REASON_INCOMPARABLE_NARRATIVE_SECTIONS,
    REASON_UNSUPPORTED_COMPARISON_METRIC,
    STAGE_COMPARABILITY,
    STAGE_FILING_DISCOVERY,
    STAGE_PATTERN_ENTRY,
    STAGE_SECTION_NAVIGATION,
    build_answer_response,
)
from disclosure_agent.agent.derived_metrics import DerivedMetric
from disclosure_agent.agent.handlers import temporal_compare as pattern_c
from disclosure_agent.agent.handlers.temporal_compare import (
    build_temporal_compare_result,
    handle_temporal_compare,
    is_narrative_plan,
)
from disclosure_agent.agent.query_analyzer import (
    INTENT_TEMPORAL_COMPARE,
    QueryPlan,
    build_query_plan,
)
from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EvidenceLocationType,
    ExecutionStatus,
    SectionScope,
)
from disclosure_agent.periodic.sections import (
    REASON_AMBIGUOUS_SECTION,
    REASON_NO_MATCHING_SECTION,
)

SYNTHETIC_COMPANIES = ("테스트반도체",)

# 합성 manifest가 담고 있는 두 사업보고서의 회계연도.
OLD_YEAR = 2024
NEW_YEAR = 2025

DOC_BY_YEAR = {OLD_YEAR: "periodic_SYNTHETIC0002", NEW_YEAR: "periodic_SYNTHETIC0001"}

RECEIPT_BY_YEAR = {OLD_YEAR: "SYNTHETIC0002", NEW_YEAR: "SYNTHETIC0001"}

NARRATIVE_QUESTION = (
    f"테스트반도체 사업 구조가 {OLD_YEAR}년이랑 {NEW_YEAR}년이랑 어떻게 달라졌어?"
)

BUSINESS_OVERVIEW_PATH = ["II. 사업의 내용", "1. 사업의 개요"]

# 원인 추론으로 읽히는 표현. 답변에 있으면 안 된다.
CAUSAL_TOKENS = ("때문에", "영향으로", "따라서", "원인", "결과로")


def _document(fiscal_year: int, *, paragraphs: list[str]) -> dict[str, Any]:
    """사업의 개요 Section 하나만 담은 합성 parsed document."""
    return {
        "metadata": {
            "doc_id": DOC_BY_YEAR[fiscal_year],
            "corp_name": "테스트반도체(주)",
            "report_name": f"사업보고서 ({fiscal_year}.12)",
            "receipt_no": RECEIPT_BY_YEAR[fiscal_year],
            "document_type": "periodic",
        },
        "sections": [
            {
                "section_id": "section_0001",
                "title": "II. 사업의 내용",
                "level": 1,
                "section_path": ["II. 사업의 내용"],
                "paragraphs": [],
            },
            {
                "section_id": "section_0002",
                "title": "1. 사업의 개요",
                "level": 2,
                "parent_section_id": "section_0001",
                "section_path": list(BUSINESS_OVERVIEW_PATH),
                "paragraphs": paragraphs,
            },
        ],
    }


# 두 해가 서로 다른 항목 표기를 쓰는 경우. 실제 코퍼스에서 관찰된 모양이다
# (2023년 "1) 2)", 2025년 "가. 나.").
UNPAIRABLE_PARAGRAPHS = {
    OLD_YEAR: ["1) 위탁생산 부문 개요입니다.", "2) 개발 서비스 부문 개요입니다."],
    NEW_YEAR: ["가. 사업개요 위탁생산 부문 개요입니다.", "나. 영업개황 개발 서비스 부문입니다."],
}


@pytest.fixture
def narrative_plan() -> QueryPlan:
    return build_query_plan(NARRATIVE_QUESTION, known_companies=SYNTHETIC_COMPANIES)


@pytest.fixture
def documents_by_year(monkeypatch: pytest.MonkeyPatch):
    """
    연도별 합성 문서를 handler의 parser 호출 자리에 꽂는다.

    문서를 경로로 가른다. 서술 경로는 두 해의 문서를 각각 열므로, 한 문서만 돌려주면 두
    연도가 같은 내용을 보게 되고 그 상태로는 짝짓기 판정을 시험할 수 없다.
    """

    def _install(paragraphs_by_year: dict[int, list[str]]) -> None:
        prepared = {
            RECEIPT_BY_YEAR[year]: _document(year, paragraphs=paragraphs)
            for year, paragraphs in paragraphs_by_year.items()
        }

        def _load(document_path):
            for receipt_no, document in prepared.items():
                if receipt_no in str(document_path):
                    return copy.deepcopy(document)

            raise AssertionError(f"준비하지 않은 문서를 열었습니다: {document_path}")

        monkeypatch.setattr(pattern_c, "load_parsed_document", _load)

    return _install


def _rewrite_manifest(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as file:
        for record in records:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")


def _version_unique(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """
    정정본이 없는 manifest 레코드만 남긴다.

    공용 합성 manifest(tests/conftest.py)는 2025 사업보고서의 원본과 정정본을 함께 담고
    있다. 서술 경로는 판본 선택 정책이 없어 정정본을 후보에 넣으므로, 그 manifest를 그대로
    쓰면 2025년이 언제나 모호해져 그 뒤의 판정을 시험할 수 없다.

    정정본을 숨기는 것이 아니다. 원본과 정정본이 함께 있는 경우는
    test_original_plus_correction_for_one_year_fails_closed가 정정본을 명시적으로 더해
    따로 고정한다.
    """
    return [record for record in records if not record.get("is_correction")]


@pytest.fixture
def narrative_manifest_path(
    synthetic_manifest_path: Path,
    manifest_records: list[dict[str, Any]],
) -> Path:
    """해마다 판본이 하나뿐인 합성 manifest. 서술 경로의 정상 흐름은 이것을 쓴다."""
    _rewrite_manifest(synthetic_manifest_path, _version_unique(manifest_records))

    return synthetic_manifest_path


# --------------------------------------------------------------------------
# 1. 경로 진입
# --------------------------------------------------------------------------


def test_canonical_question_reaches_the_narrative_path(
    narrative_plan: QueryPlan,
) -> None:
    """범위를 밝힌 시계열 질문이 서술 경로로 분류된다."""
    assert narrative_plan.intent == INTENT_TEMPORAL_COMPARE
    assert narrative_plan.section_scope is SectionScope.BUSINESS_OVERVIEW
    assert narrative_plan.metric is None
    assert narrative_plan.derived_metric is None
    assert is_narrative_plan(narrative_plan) is True


def test_metric_less_plan_without_a_scope_stays_unsupported(
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    지표도 범위도 없는 시계열 계획은 그대로 미지원이다.

    서술 경로를 지표의 부재로 가르지 않는다는 것을 결과로 고정한다. 이 계획이 서술 경로로
    들어가면 질문이 지정하지도 않은 Section을 견주게 된다.
    """
    plan = build_query_plan(
        f"테스트반도체 {OLD_YEAR}년이랑 {NEW_YEAR}년이랑 어떻게 달라졌어?",
        known_companies=SYNTHETIC_COMPANIES,
    )

    assert plan.section_scope is None
    assert is_narrative_plan(plan) is False

    result = handle_temporal_compare(
        plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED
    assert result.answerability_status is None
    assert result.execution_issue is not None
    assert result.execution_issue.reason == "unsupported_comparison_metric"


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("서술 범위 + 원천 지표", {"metric": "revenue"}),
        ("서술 범위 + 파생 개념", {"derived_metric": DerivedMetric.OPERATING_MARGIN}),
    ],
)
def test_mixed_scope_and_metric_plan_is_not_narrative(
    label: str,
    overrides: dict[str, Any],
    narrative_plan: QueryPlan,
) -> None:
    """
    서술 범위와 지표를 함께 담은 계획은 서술 경로가 아니다.

    범위만 보고 서술 경로로 보내면 질문이 함께 밝힌 지표가 조용히 사라진다. 판정은 순수한
    서술 모양(범위 있음, 지표 없음, 파생 개념 없음)에서만 참이어야 한다.
    """
    mixed = replace(narrative_plan, **overrides)

    assert mixed.section_scope is SectionScope.BUSINESS_OVERVIEW, label
    assert is_narrative_plan(mixed) is False, label


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("서술 범위 + 원천 지표", {"metric": "revenue"}),
        ("서술 범위 + 파생 개념", {"derived_metric": DerivedMetric.OPERATING_MARGIN}),
    ],
)
def test_mixed_scope_and_metric_plan_fails_closed_without_discarding_either(
    label: str,
    overrides: dict[str, Any],
    narrative_plan: QueryPlan,
    documents_by_year,
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    범위와 지표를 함께 담은 계획은 어느 경로로도 답하지 않는다.

    셋을 함께 고정한다.
        서술 경로로 새지 않는다   NOT_COMPARABLE이 나오면 지표를 버리고 서술을 견준 것이다.
        수치 경로로 새지 않는다   Fact나 Calculation이 나오면 범위를 버리고 값을 견준 것이다.
        입구에서 막힌다          코퍼스를 보기 전이므로 답변 가능성을 판정하지 않는다.

    매출은 수치 경로가 지원하는 지표라, 범위 검사가 없으면 그대로 통과해 Q07처럼 답한다.
    그 경우가 이 테스트가 막으려는 것이다.
    """
    documents_by_year(UNPAIRABLE_PARAGRAPHS)

    mixed = replace(narrative_plan, **overrides)

    result = handle_temporal_compare(
        mixed,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.BLOCKED, label
    assert result.answerability_status is None, label
    assert result.answerability_reason is not AnswerabilityReason.NOT_COMPARABLE, label

    issue = result.execution_issue

    assert issue is not None, label
    assert issue.stage == STAGE_PATTERN_ENTRY, label
    assert issue.reason == REASON_UNSUPPORTED_COMPARISON_METRIC, label

    # 어느 쪽 요청도 버리고 답하지 않았다.
    assert result.facts == [], label
    assert result.calculations == [], label
    assert result.evidence == [], label
    assert result.narrative_comparisons == [], label


def test_numeric_plans_without_a_scope_are_unaffected_by_the_narrative_gate() -> None:
    """
    범위가 없는 수치 계획(Q07·Q08)은 서술 판정에 걸리지 않는다.

    서술 판정이 범위를 먼저 보므로, 범위가 비어 있는 계획은 그 분기에 들어가지 않는다. 그
    사실을 계획 모양으로 고정한다. 실행 결과는 test_pattern_c.py가 그대로 고정한다.
    """
    q07_like = QueryPlan(
        question="합성 질문",
        companies=("테스트반도체",),
        years=(OLD_YEAR, NEW_YEAR),
        intent=INTENT_TEMPORAL_COMPARE,
        metric="revenue",
    )
    # QueryPlan.metric의 기본값이 비어 있지 않으므로 파생 개념 계획은 metric을 명시적으로
    # 비운다. test_pattern_c.py의 _temporal_plan이 같은 이유로 같은 모양을 쓴다.
    q08_like = QueryPlan(
        question="합성 질문",
        companies=("테스트반도체",),
        years=(OLD_YEAR, NEW_YEAR),
        intent=INTENT_TEMPORAL_COMPARE,
        metric=None,
        derived_metric=DerivedMetric.OPERATING_MARGIN,
    )

    for plan in (q07_like, q08_like):
        assert plan.section_scope is None
        assert is_narrative_plan(plan) is False
        assert pattern_c.resolve_shape(plan) is not None


# --------------------------------------------------------------------------
# 2. 양쪽 확보와 비교 불가 판정
# --------------------------------------------------------------------------


@pytest.fixture
def unpairable_result(
    narrative_plan: QueryPlan,
    documents_by_year,
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
):
    documents_by_year(UNPAIRABLE_PARAGRAPHS)

    return handle_temporal_compare(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )


def test_unpairable_sections_are_completed_unanswerable_not_comparable(
    unpairable_result,
) -> None:
    assert unpairable_result.execution_status is ExecutionStatus.COMPLETED
    assert unpairable_result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert unpairable_result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE


def test_not_comparable_keeps_the_narrative_diagnosis(unpairable_result) -> None:
    """
    canonical 사유 뒤의 진단이 남는다.

    NOT_COMPARABLE만 보면 수치가 어긋난 것인지 서술을 짝짓지 못한 것인지 알 수 없고, 후속
    작업이 다르다.
    """
    detail = unpairable_result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_COMPARABILITY
    assert detail.reason == REASON_INCOMPARABLE_NARRATIVE_SECTIONS


def test_evidence_is_retained_from_both_fiscal_years(unpairable_result) -> None:
    """
    양쪽 연도의 근거가 모두 남는다.

    한쪽만 남으면 견주지 못했다는 판정의 근거가 절반만 남고, 어느 쪽을 확보했는지 결과만
    봐서는 알 수 없다.
    """
    doc_ids = {item.doc_id for item in unpairable_result.evidence}

    assert doc_ids == {DOC_BY_YEAR[OLD_YEAR], DOC_BY_YEAR[NEW_YEAR]}
    assert len(unpairable_result.evidence) >= 2

    for item in unpairable_result.evidence:
        assert item.location_type is EvidenceLocationType.TEXT
        assert list(item.section_path) == BUSINESS_OVERVIEW_PATH


def test_narrative_path_creates_no_values(unpairable_result) -> None:
    """
    값을 하나도 만들지 않는다.

    수치 결과가 하나라도 섞이면 서술 비교가 계산 경로를 지난 것이 되고, Q09가 계산을 지나지
    않는다는 규약이 깨진다.
    """
    assert unpairable_result.facts == []
    assert unpairable_result.calculations == []
    assert unpairable_result.comparisons == []
    assert unpairable_result.narrative_comparisons == []
    assert unpairable_result.document_summaries == []


@pytest.mark.parametrize(
    ("label", "paragraphs_by_year"),
    [
        # 두 해가 서로 다른 항목 표기를 쓰는 경우.
        ("서로 다른 표기", UNPAIRABLE_PARAGRAPHS),
        # 항목 본문이 양쪽에 똑같이 있고 앞에 단락 하나가 끼어든 경우.
        (
            "같은 본문 + 삽입",
            {
                OLD_YEAR: ["가. 사업개요 위탁생산 부문입니다."],
                NEW_YEAR: [
                    "새로 끼어든 머리말입니다.",
                    "가. 사업개요 위탁생산 부문입니다.",
                ],
            },
        ),
        # 양쪽 본문이 완전히 같은 경우.
        (
            "완전히 같은 본문",
            {
                OLD_YEAR: ["가. 사업개요 위탁생산 부문입니다."],
                NEW_YEAR: ["가. 사업개요 위탁생산 부문입니다."],
            },
        ),
    ],
)
def test_verdict_does_not_depend_on_section_contents(
    label: str,
    paragraphs_by_year: dict[int, list[str]],
    narrative_plan: QueryPlan,
    documents_by_year,
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    판정은 문서 내용이 아니라 이 구현이 가진 규칙에서 나온다.

    승인된 항목 짝짓기 규칙이 없으므로 두 기재가 서로 다르든, 일부가 같든, 완전히 같든
    결과는 같아야 한다. 내용에 따라 갈리면 그것은 짝짓기 규칙이 있다는 뜻이고, 이 구현에는
    없다.

    특히 마지막 경우를 변화 없음으로 답하지 않는다. 본문이 같다는 것과 항목을 짝지어 견준
    끝에 변화가 없었다는 것은 다르며, 둘을 같은 답으로 내보내는 것이 이 문항의 주요 실패
    모드다(representative_questions.md Q09).
    """
    documents_by_year(paragraphs_by_year)

    result = handle_temporal_compare(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED, label
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE, label
    assert result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE, label
    assert result.narrative_comparisons == [], label
    assert {item.doc_id for item in result.evidence} == {
        DOC_BY_YEAR[OLD_YEAR],
        DOC_BY_YEAR[NEW_YEAR],
    }, label


def test_diagnostic_does_not_claim_a_content_derived_verdict(
    unpairable_result,
) -> None:
    """
    진단이 문서에서 무엇을 세어 판정한 것처럼 말하지 않는다.

    "공유 항목 N건이라 짝지을 수 없다"는 N이 0보다 크면 성립하지 않는 문장이다. 판정의
    근거는 규칙의 부재이므로 진단도 그것을 말해야 한다.
    """
    detail = unpairable_result.answerability_detail.detail

    assert "양쪽 모두 확보했습니다" in detail
    assert "규칙이 이 구현에 아직 없어" in detail
    assert "변화가 없다는 판정이 아닙니다" in detail

    assert "공유" not in detail
    assert "건이라" not in detail


# --------------------------------------------------------------------------
# 3. 한쪽을 확보하지 못한 경우 — 기존 Section 탐색 사유를 그대로 쓴다
# --------------------------------------------------------------------------


def test_missing_section_on_one_side_fails_closed(
    narrative_plan: QueryPlan,
    documents_by_year,
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    한쪽에 범위가 없으면 남은 한쪽으로 답하지 않는다.

    사유는 periodic.sections의 것을 그대로 쓴다. 같은 상황이 Pattern B와 다른 이름을 갖지
    않게 하기 위해서다.
    """
    documents_by_year({OLD_YEAR: [], NEW_YEAR: UNPAIRABLE_PARAGRAPHS[NEW_YEAR]})

    result = handle_temporal_compare(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.answerability_reason is not AnswerabilityReason.NOT_COMPARABLE
    assert result.narrative_comparisons == []

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_SECTION_NAVIGATION


def test_ambiguous_section_on_one_side_fails_closed(
    narrative_plan: QueryPlan,
    documents_by_year,
    monkeypatch: pytest.MonkeyPatch,
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """범위 후보가 여럿이면 하나를 골라 견주지 않는다."""
    documents_by_year(UNPAIRABLE_PARAGRAPHS)

    original = pattern_c.load_parsed_document

    def _duplicated(document_path):
        document = original(document_path)

        if RECEIPT_BY_YEAR[NEW_YEAR] in str(document_path):
            duplicate = copy.deepcopy(document["sections"][1])
            duplicate["section_id"] = "section_0099"
            duplicate["section_path"] = ["II. 사업의 내용", "8. 사업의 개요 보충"]
            document["sections"].append(duplicate)

        return document

    monkeypatch.setattr(pattern_c, "load_parsed_document", _duplicated)

    result = handle_temporal_compare(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_SECTION_NAVIGATION
    assert detail.reason == REASON_AMBIGUOUS_SECTION
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH


def test_missing_filing_for_one_year_is_not_downgraded_to_partial(
    narrative_plan: QueryPlan,
    manifest_records: list[dict[str, Any]],
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    한 해의 공시가 없으면 부분 답변으로 낮추지 않는다.

    확보한 한 해의 서술을 일부 답으로 내보내면, 견주지 못한 결과가 견준 결과처럼 읽힌다.
    """
    remaining = [
        record
        for record in _version_unique(manifest_records)
        if record["rcept_no"] != RECEIPT_BY_YEAR[OLD_YEAR]
    ]

    _rewrite_manifest(narrative_manifest_path, remaining)

    result = handle_temporal_compare(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.answerability_status is not AnswerabilityStatus.PARTIALLY_ANSWERABLE
    assert result.answerability_status is AnswerabilityStatus.UNANSWERABLE
    assert result.answerability_reason is AnswerabilityReason.NOT_FOUND

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_FILING_DISCOVERY


def test_ambiguous_filing_is_not_downgraded_to_partial(
    narrative_plan: QueryPlan,
    documents_by_year,
    manifest_records: list[dict[str, Any]],
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    같은 해의 후보가 둘이면 접수번호로 고르지 않는다.

    먼저 처리하는 연도는 정상적으로 확보돼야 뒤쪽 연도의 판정에 도달한다. 그래서 문서 stub을
    함께 건다. 걸지 않으면 앞 연도가 parse_failed로 멈춰 이 검증이 다른 정지를 보게 된다.
    """
    documents_by_year(UNPAIRABLE_PARAGRAPHS)

    duplicate = copy.deepcopy(
        next(
            record
            for record in manifest_records
            if record["rcept_no"] == RECEIPT_BY_YEAR[NEW_YEAR]
        )
    )
    duplicate["rcept_no"] = "SYNTHETIC9001"
    duplicate["doc_id"] = "periodic_SYNTHETIC9001"

    _rewrite_manifest(
        narrative_manifest_path, [*_version_unique(manifest_records), duplicate]
    )

    result = handle_temporal_compare(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_status is not AnswerabilityStatus.PARTIALLY_ANSWERABLE


def test_original_plus_correction_for_one_year_fails_closed(
    narrative_plan: QueryPlan,
    documents_by_year,
    manifest_records: list[dict[str, Any]],
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    같은 해에 원본과 정정본이 함께 있으면 원본을 고르지 않는다.

    판본 선택 정책(D5)이 없으므로 정정본을 조회에서 빼는 것 자체가 원본을 고르는 정책이 된다.
    정정본을 후보에 넣고, 둘이면 어느 것도 고르지 않는다.
    """
    documents_by_year(UNPAIRABLE_PARAGRAPHS)

    original = next(
        record
        for record in manifest_records
        if record["rcept_no"] == RECEIPT_BY_YEAR[NEW_YEAR]
    )
    correction = copy.deepcopy(original)
    correction["rcept_no"] = "SYNTHETIC0001C"
    correction["doc_id"] = "periodic_SYNTHETIC0001C"
    correction["is_correction"] = True
    correction["report_nm"] = f"[기재정정]{original['report_nm']}"

    _rewrite_manifest(
        narrative_manifest_path, [*_version_unique(manifest_records), correction]
    )

    result = handle_temporal_compare(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_status is AnswerabilityStatus.UNRESOLVED
    assert result.answerability_reason is AnswerabilityReason.AMBIGUOUS_MATCH
    assert result.answerability_status is not AnswerabilityStatus.PARTIALLY_ANSWERABLE

    detail = result.answerability_detail

    assert detail is not None
    assert detail.stage == STAGE_FILING_DISCOVERY
    assert detail.reason == REASON_AMBIGUOUS_FILING
    assert "정정본 포함" in detail.detail

    # 원본을 조용히 고르지 않았다. 원본 문서의 근거가 결과에 있으면 고른 것이다.
    doc_ids = {item.doc_id for item in result.evidence}

    assert DOC_BY_YEAR[NEW_YEAR] not in doc_ids
    assert "periodic_SYNTHETIC0001C" not in doc_ids
    assert result.narrative_comparisons == []


def test_single_filing_per_year_proceeds_with_corrections_included(
    unpairable_result,
) -> None:
    """
    정정본을 후보에 넣어도 해마다 1건이면 그대로 진행한다.

    합성 manifest에는 정정본이 없으므로 두 해 모두 1건이고, 비교 불가 판정까지 도달한다.
    이 테스트가 없으면 정정본 포함이 모든 연도를 모호하게 만드는 회귀를 잡지 못한다.
    """
    assert unpairable_result.execution_status is ExecutionStatus.COMPLETED
    assert unpairable_result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE
    assert {item.doc_id for item in unpairable_result.evidence} == {
        DOC_BY_YEAR[OLD_YEAR],
        DOC_BY_YEAR[NEW_YEAR],
    }


# --------------------------------------------------------------------------
# 4. 문장
# --------------------------------------------------------------------------


def test_answer_states_both_sections_were_found_without_asserting_no_change(
    unpairable_result,
) -> None:
    """
    문장이 말해야 하는 것과 말하면 안 되는 것을 함께 고정한다.

    "변화가 없다"고 적으면 확인하지 않은 사실을 단정하게 되고, 그것이 이 문항의 주요 실패
    모드다(representative_questions.md Q09).
    """
    response = build_answer_response(unpairable_result)

    assert response.status is AnswerabilityStatus.UNANSWERABLE
    assert response.reason is AnswerabilityReason.NOT_COMPARABLE
    assert response.answer

    assert "찾았습니다" in response.answer
    assert "단정하지 않습니다" in response.answer

    assert "변화가 없었다는 뜻은 아니며" in response.answer
    assert "변화가 없습니다" not in response.answer
    assert "동일합니다" not in response.answer

    for token in CAUSAL_TOKENS:
        assert token not in response.answer


def test_numeric_not_comparable_message_is_unchanged() -> None:
    """
    수치 경로의 비교 불가 문장은 그대로다.

    detail로 문장을 가르는 자리를 만들었으므로, 기존 사유가 새 문장으로 새지 않는지 함께
    고정한다.
    """
    from disclosure_agent.agent.answer_builder import (
        REASON_INCOMPARABLE_PERIODS,
        UNAVAILABLE_MESSAGE_BY_REASON,
    )
    from disclosure_agent.common.schemas import (
        AgentExecutionResult,
        AnswerabilityDetail,
    )

    numeric = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.UNANSWERABLE,
        answerability_reason=AnswerabilityReason.NOT_COMPARABLE,
        answerability_detail=AnswerabilityDetail(
            stage=STAGE_COMPARABILITY,
            reason=REASON_INCOMPARABLE_PERIODS,
            detail="합성 진단",
        ),
    )

    assert (
        build_answer_response(numeric).answer
        == UNAVAILABLE_MESSAGE_BY_REASON[AnswerabilityReason.NOT_COMPARABLE]
    )


# --------------------------------------------------------------------------
# 5. 질문 문자열부터 실행 결과까지
# --------------------------------------------------------------------------


def test_router_reaches_the_narrative_path_from_the_question_string(
    documents_by_year,
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    documents_by_year(UNPAIRABLE_PARAGRAPHS)

    result = router.answer_question(
        NARRATIVE_QUESTION,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert result.answerability_reason is AnswerabilityReason.NOT_COMPARABLE
    assert {item.doc_id for item in result.evidence} == {
        DOC_BY_YEAR[OLD_YEAR],
        DOC_BY_YEAR[NEW_YEAR],
    }


def test_narrative_result_is_deterministic_across_runs(
    narrative_plan: QueryPlan,
    documents_by_year,
    narrative_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    documents_by_year(UNPAIRABLE_PARAGRAPHS)

    first = build_temporal_compare_result(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    second = build_temporal_compare_result(
        narrative_plan,
        corpus_root=nfd_corpus_root,
        manifest_path=narrative_manifest_path,
    )

    assert first.to_dict() == second.to_dict()


def test_no_matching_section_reason_is_reported_verbatim() -> None:
    """periodic.sections의 사유 문자열을 handler가 새로 만들지 않는다."""
    assert REASON_NO_MATCHING_SECTION == "no_matching_section"
    assert REASON_AMBIGUOUS_SECTION == "ambiguous_section"
