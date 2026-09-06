"""Q01 vertical slice 단위 테스트.

대상 질문:
    "한미반도체 2025년 연결 기준 매출액이 얼마야?"

이 테스트는 합성 fixture만 사용한다. 실제 공시 XML과 실제 수치는 쓰지 않는다.
fixture는 Parser의 최종 Data Contract가 아니라 Q01 downstream 코드가 요구하는
최소 입력 조건을 고정한 것이다.
"""

from __future__ import annotations

import unicodedata
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent.answer_builder import (
    STATUS_BLOCKED,
    STATUS_OK,
    build_structured_result,
)
from disclosure_agent.agent.evidence import build_evidence
from disclosure_agent.agent.handlers.single_fact import build_result_from_document
from disclosure_agent.agent.query_analyzer import (
    METRIC_REVENUE,
    SCOPE_CONSOLIDATED,
    SCOPE_SEPARATE,
    QueryPlan,
    UnsupportedQuestionError,
    build_query_plan,
    normalize_company_name,
)
from disclosure_agent.periodic.extractor import (
    TABLE_CLASS_NOTE,
    TABLE_CLASS_PRIMARY_STATEMENT,
    ExtractionBlockedError,
    build_table_group_index,
    classify_table,
    extract_revenue_fact,
    parse_accounting_context,
    parse_numeric_value,
    parse_unit,
    resolve_table_unit,
    table_class_priority,
)
from disclosure_agent.retrieval.metadata_filter import (
    FilingNotFoundError,
    filter_filings,
    find_main_document,
    list_company_names,
    locate_filing,
    resolve_existing_path,
    select_latest_filing,
)

Q01_QUESTION = "한미반도체 2025년 연결 기준 매출액이 얼마야?"

# 인식 후보 목록은 호출자가 준다. 운영 경로에서는 service가 manifest에서 읽어 넘긴다.
Q01_KNOWN_COMPANIES = ("한미반도체",)


# --------------------------------------------------------------------------
# 1. QueryPlan
# --------------------------------------------------------------------------


def test_q01_question_builds_deterministic_query_plan() -> None:
    plan = build_query_plan(Q01_QUESTION, known_companies=Q01_KNOWN_COMPANIES)

    assert plan.company == "한미반도체"
    assert plan.year == 2025
    assert plan.document_class == "periodic"
    assert plan.document_subtype == "annual"
    assert plan.accounting_scope == SCOPE_CONSOLIDATED
    assert plan.metric == METRIC_REVENUE
    assert plan.accounting_scope_explicit is True
    assert plan.warnings == ()


def test_query_plan_is_repeatable() -> None:
    """같은 질문은 항상 같은 계획을 만든다(LLM 비결정성 없음)."""
    assert build_query_plan(
        Q01_QUESTION, known_companies=Q01_KNOWN_COMPANIES
    ) == build_query_plan(Q01_QUESTION, known_companies=Q01_KNOWN_COMPANIES)


def test_query_plan_detects_separate_scope() -> None:
    plan = build_query_plan(
        "테스트반도체 2025년 별도 기준 매출액",
        known_companies=["테스트반도체"],
    )

    assert plan.accounting_scope == SCOPE_SEPARATE
    assert plan.accounting_scope_explicit is True


def test_query_plan_defaults_to_consolidated_and_warns() -> None:
    plan = build_query_plan(
        "테스트반도체 2025년 매출액",
        known_companies=["테스트반도체"],
    )

    assert plan.accounting_scope == SCOPE_CONSOLIDATED
    assert plan.accounting_scope_explicit is False
    assert plan.warnings  # 기본값으로 채웠음을 경고로 남긴다


def test_query_plan_uses_known_companies_and_does_not_guess() -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan("없는회사 2025년 연결 매출액", known_companies=["테스트반도체"])

    assert error.value.reason == "unknown_company"


def test_query_plan_prefers_longest_company_match() -> None:
    plan = build_query_plan(
        "한미반도체 2025년 연결 매출액",
        known_companies=["한미", "한미반도체"],
    )

    assert plan.company == "한미반도체"


@pytest.mark.parametrize(
    ("question", "reason"),
    [
        ("한미반도체 2025년 1분기 연결 매출액", "unsupported_period"),
        ("한미반도체 연결 기준 매출액", "missing_year"),
        ("한미반도체 2025년 연결 기준 임직원 수", "unsupported_metric"),
        ("   ", "empty_question"),
    ],
)
def test_query_plan_rejects_out_of_scope_questions(question: str, reason: str) -> None:
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(question, known_companies=Q01_KNOWN_COMPANIES)

    assert error.value.reason == reason


def test_query_plan_requires_company_universe() -> None:
    """기업명 목록 없이는 기업을 추측하지 않고 거절한다."""
    with pytest.raises(UnsupportedQuestionError) as error:
        build_query_plan(Q01_QUESTION)

    assert error.value.reason == "missing_company_universe"


# --------------------------------------------------------------------------
# 2. 회사명 정규화
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [
        "한미반도체",
        "한미반도체(주)",
        "(주)한미반도체",
        "한미반도체 주식회사",
        "한미반도체  ",
        unicodedata.normalize("NFD", "한미반도체"),
    ],
)
def test_company_name_normalization_collapses_legal_suffixes(raw: str) -> None:
    assert normalize_company_name(raw) == normalize_company_name("한미반도체")


def test_company_name_normalization_keeps_distinct_names_apart() -> None:
    assert normalize_company_name("한미반도체") != normalize_company_name("한미약품")


# --------------------------------------------------------------------------
# 3. manifest 필터링
# --------------------------------------------------------------------------


def test_filter_filings_matches_across_legal_suffix(
    manifest_records: list[dict[str, Any]],
) -> None:
    """질문의 '테스트반도체'가 manifest의 '테스트반도체(주)'와 매칭돼야 한다."""
    matched = filter_filings(
        manifest_records,
        company="테스트반도체",
        year=2025,
        document_class="periodic",
        document_subtype="annual",
    )

    assert [record["doc_id"] for record in matched] == ["periodic_SYNTHETIC0001"]


def test_filter_filings_excludes_other_year_subtype_group_and_corrections(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company="테스트반도체(주)",
        year=2025,
        document_class="periodic",
        document_subtype="annual",
    )

    doc_ids = {record["doc_id"] for record in matched}

    assert "periodic_SYNTHETIC0002" not in doc_ids  # 2024년
    assert "periodic_SYNTHETIC0003" not in doc_ids  # 분기보고서
    assert "periodic_SYNTHETIC0004" not in doc_ids  # 정정공시
    assert "periodic_SYNTHETIC0005" not in doc_ids  # 다른 기업
    assert "exchange_SYNTHETIC0006" not in doc_ids  # 거래소공시


def test_filter_filings_can_include_corrections(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company="테스트반도체",
        year=2025,
        document_class="periodic",
        document_subtype="annual",
        include_corrections=True,
    )

    assert len(matched) == 2


def test_select_latest_filing_picks_highest_receipt_no(
    manifest_records: list[dict[str, Any]],
) -> None:
    matched = filter_filings(
        manifest_records,
        company="테스트반도체",
        year=2025,
        document_class="periodic",
        document_subtype="annual",
        include_corrections=True,
    )

    assert select_latest_filing(matched)["rcept_no"] == "SYNTHETIC0009"


def test_list_company_names_deduplicates(manifest_records: list[dict[str, Any]]) -> None:
    names = list_company_names(manifest_records)

    assert set(names) == {"테스트반도체(주)", "테스트전자"}


# --------------------------------------------------------------------------
# 4. Unicode 경로 해석 (D-1)
# --------------------------------------------------------------------------


def test_resolve_existing_path_finds_nfd_directory_from_nfc_manifest_path(
    nfd_corpus_root: Path,
) -> None:
    """manifest는 NFC, 파일시스템은 NFD인 상황을 해석할 수 있어야 한다."""
    relative = unicodedata.normalize(
        "NFC", "raw/periodic/테스트반도체/SYNTHETIC0001_annual_2025_12"
    )

    resolved = resolve_existing_path(nfd_corpus_root, relative)

    assert resolved is not None
    assert resolved.is_dir()
    assert (resolved / "SYNTHETIC0001.xml").exists()


def test_resolve_existing_path_also_accepts_nfd_input(nfd_corpus_root: Path) -> None:
    relative = unicodedata.normalize(
        "NFD", "raw/periodic/테스트반도체/SYNTHETIC0001_annual_2025_12"
    )

    assert resolve_existing_path(nfd_corpus_root, relative) is not None


def test_resolve_existing_path_returns_none_when_absent(nfd_corpus_root: Path) -> None:
    assert resolve_existing_path(nfd_corpus_root, "raw/periodic/없는회사/없는문서") is None


def test_find_main_document_prefers_receipt_no_over_attachments(
    nfd_corpus_root: Path,
) -> None:
    directory = resolve_existing_path(
        nfd_corpus_root, "raw/periodic/테스트반도체/SYNTHETIC0001_annual_2025_12"
    )

    main = find_main_document(directory, "SYNTHETIC0001")

    assert main is not None
    assert main.name == "SYNTHETIC0001.xml"


def test_locate_filing_end_to_end_resolves_path(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    plan = build_query_plan(
        "테스트반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=["테스트반도체"],
    )

    filing = locate_filing(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=plan.company,
        document_class=plan.document_class,
        document_subtype=plan.document_subtype,
        year=plan.year,
    )

    assert filing.doc_id == "periodic_SYNTHETIC0001"
    assert filing.corp_name == "테스트반도체(주)"
    assert filing.receipt_no == "SYNTHETIC0001"
    assert filing.is_correction is False
    assert filing.main_document.exists()


def test_locate_filing_raises_when_no_manifest_match(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    plan = QueryPlan(
        question="테스트반도체 2019년 연결 매출액",
        companies=("테스트반도체",),
        years=(2019,),
    )

    with pytest.raises(FilingNotFoundError) as error:
        locate_filing(
            manifest_path=synthetic_manifest_path,
            corpus_root=nfd_corpus_root,
            company=plan.company,
            document_class=plan.document_class,
            document_subtype=plan.document_subtype,
            year=plan.year,
        )

    assert error.value.reason == "no_matching_filing"


# --------------------------------------------------------------------------
# 5. ACONTEXT 해석
# --------------------------------------------------------------------------


def test_parse_accounting_context_reads_year_and_scope() -> None:
    context = parse_accounting_context(
        "CFY2025dFY_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis"
        "_ifrs-full_ConsolidatedMember"
    )

    assert context is not None
    assert context.fiscal_year == 2025
    assert context.accounting_scope == SCOPE_CONSOLIDATED
    assert context.period_code == "dFY"
    assert context.is_full_year_duration is True
    assert context.axis_depth == 1


def test_parse_accounting_context_reads_separate_member() -> None:
    context = parse_accounting_context(
        "PFY2024dFY_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis"
        "_ifrs-full_SeparateMember"
    )

    assert context is not None
    assert context.fiscal_year == 2024
    assert context.accounting_scope == SCOPE_SEPARATE


def test_parse_accounting_context_counts_extra_axes() -> None:
    context = parse_accounting_context(
        "CFY2025dFY_ifrs-full_ConsolidatedAndSeparateFinancialStatementsAxis"
        "_ifrs-full_ConsolidatedMember_ifrs-full_OperatingSegmentsAxis_dart_SegmentAMember"
    )

    assert context is not None
    assert context.axis_depth == 2


def test_parse_accounting_context_rejects_garbage() -> None:
    assert parse_accounting_context("") is None
    assert parse_accounting_context("not-a-context") is None


# --------------------------------------------------------------------------
# 6. 단위 해석 (D-3)
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "token", "multiplier"),
    [
        ("(단위 : 원)", "원", 1),
        ("(단위 : 천원)", "천원", 1_000),
        ("(단위:천원)", "천원", 1_000),
        ("(단위 : 백만원)", "백만원", 1_000_000),
        ("(단위 : 백만원, %, 회)", "백만원", 1_000_000),
    ],
)
def test_parse_unit_reads_currency_multiplier(text: str, token: str, multiplier: int) -> None:
    unit = parse_unit(text)

    assert unit is not None
    assert unit.token == token
    assert unit.multiplier == multiplier
    assert unit.trusted is True


def test_parse_unit_flags_untrusted_when_table_body_leaked_in() -> None:
    """표 본문에서 '단위'를 잘못 집어온 문자열은 신뢰할 수 없다고 표시한다."""
    unit = parse_unit("기본주당이익(손실) (단위 : 원) 2,256 1,589 2,753")

    assert unit is not None
    assert unit.trusted is False


def test_parse_unit_returns_none_without_declaration() -> None:
    assert parse_unit(None) is None
    assert parse_unit("") is None
    assert parse_unit("구 분") is None


# --------------------------------------------------------------------------
# 7. 값 파싱
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("1,234,567", Decimal("1234567")),
        ("1234567", Decimal("1234567")),
        ("(1,234)", Decimal("-1234")),
        ("△1,234", Decimal("-1234")),
        ("-1,234", Decimal("-1234")),
        ("0", Decimal("0")),
        ("1,234.5", Decimal("1234.5")),
    ],
)
def test_parse_numeric_value_handles_korean_table_formats(raw: str, expected: Decimal) -> None:
    assert parse_numeric_value(raw) == expected


def test_parse_numeric_value_applies_anegated_flag() -> None:
    assert parse_numeric_value("1,234", negated=True) == Decimal("-1234")


@pytest.mark.parametrize("raw", ["", "   ", "-", "해당사항 없음", None])
def test_parse_numeric_value_returns_none_for_non_numeric(raw: str | None) -> None:
    assert parse_numeric_value(raw) is None


# --------------------------------------------------------------------------
# 8. Revenue Fact 추출
# --------------------------------------------------------------------------


def test_extract_revenue_selects_revenue_acode_not_cost_of_sales(
    synthetic_document: dict[str, Any],
) -> None:
    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert fact.acode == "ifrs-full_Revenue"
    assert fact.metric == METRIC_REVENUE
    assert fact.raw_value != "800,000"  # 매출원가 값이 아니다


def test_extract_revenue_prefers_consolidated_when_both_scopes_present(
    synthetic_document: dict[str, Any],
) -> None:
    """연결과 별도가 같이 있을 때 연결을 고른다."""
    fact = extract_revenue_fact(
        synthetic_document,
        fiscal_year=2025,
        accounting_scope=SCOPE_CONSOLIDATED,
    )

    assert fact.accounting_scope == SCOPE_CONSOLIDATED
    assert fact.raw_value == "1,234,567"
    assert fact.raw_value != "1,111,111"  # 별도 값이 아니다


def test_extract_revenue_can_select_separate_scope(
    synthetic_document: dict[str, Any],
) -> None:
    fact = extract_revenue_fact(
        synthetic_document,
        fiscal_year=2025,
        accounting_scope=SCOPE_SEPARATE,
    )

    assert fact.accounting_scope == SCOPE_SEPARATE
    assert fact.raw_value == "1,111,111"
    assert fact.location.table_id == "table_0003"


def test_extract_revenue_picks_requested_year_when_multiple_years_present(
    synthetic_document: dict[str, Any],
) -> None:
    """FY2024와 FY2025가 같은 행에 있을 때 요청 연도를 고른다."""
    fy2025 = extract_revenue_fact(synthetic_document, fiscal_year=2025)
    fy2024 = extract_revenue_fact(synthetic_document, fiscal_year=2024)

    assert fy2025.fiscal_year == 2025
    assert fy2025.raw_value == "1,234,567"

    assert fy2024.fiscal_year == 2024
    assert fy2024.raw_value == "1,000,000"

    assert fy2025.location.column_index != fy2024.location.column_index


def test_extract_revenue_prefers_aggregate_over_segment_axis(
    synthetic_document: dict[str, Any],
) -> None:
    """부문(segment) 축이 붙은 값보다 집계된 값을 고른다."""
    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert fact.raw_value == "1,234,567"
    assert "OperatingSegmentsAxis" not in fact.acontext


def test_extract_revenue_ignores_instant_context(
    synthetic_document: dict[str, Any],
) -> None:
    """기말 시점(eFY) 컨텍스트는 연간 매출로 쓰지 않는다."""
    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert fact.raw_value != "9,999,999"
    assert fact.acontext.startswith("CFY2025dFY")


def test_extract_revenue_keeps_table_unit_and_computes_base_value(
    synthetic_document: dict[str, Any],
) -> None:
    """표 단위를 유지하고 원 단위 환산값을 함께 남긴다."""
    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert fact.unit_text == "(단위 : 백만원)"
    assert fact.unit_token == "백만원"
    assert fact.unit_multiplier == 1_000_000
    assert fact.value == Decimal("1234567")
    assert fact.value_in_base_unit == Decimal("1234567") * 1_000_000
    assert fact.base_unit == "원"


def test_extract_revenue_blocks_when_acontext_missing(
    synthetic_document: dict[str, Any],
) -> None:
    """ACONTEXT가 없으면 추측하지 않고 막힌 이유를 알린다 (D-2)."""
    for table in synthetic_document["tables"]:
        for row in table["rows"]:
            for cell in row:
                cell.pop("acontext", None)

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert error.value.reason == "missing_acontext"


def test_extract_revenue_blocks_when_table_unit_untrusted(
    synthetic_document: dict[str, Any],
) -> None:
    """표 단위가 본문에서 잘못 수집된 형태면 값을 쓰지 않는다 (D-3)."""
    for table in synthetic_document["tables"]:
        table["unit"] = "기본주당이익(손실) (단위 : 원) 2,256 1,589 2,753"

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert error.value.reason == "untrusted_table_unit"


def test_extract_revenue_blocks_when_table_unit_missing(
    synthetic_document: dict[str, Any],
) -> None:
    for table in synthetic_document["tables"]:
        table["unit"] = None

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert error.value.reason == "missing_table_unit"


def test_extract_revenue_blocks_when_no_metric_evidence_exists() -> None:
    document = {
        "metadata": {
            "doc_id": "periodic_SAMPLE",
            "corp_name": "테스트기업",
            "report_name": "사업보고서",
        },
        "sections": [],
        "table_groups": [],
        "tables": [],
    }

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(document, fiscal_year=2025)

    assert error.value.reason == "no_matching_label_fallback"


def test_extract_revenue_blocks_for_year_without_context(
    synthetic_document: dict[str, Any],
) -> None:
    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(synthetic_document, fiscal_year=2019)

    assert error.value.reason == "no_matching_context"


# --------------------------------------------------------------------------
# 8-b. 본표/주석 후보 모호성 (실제 사업보고서에서 발견)
#
# 같은 ACODE, 같은 ACONTEXT, 같은 axis_depth, 같은 row_index를 가진 값이
# 재무제표 본표와 주석표에 동시에 존재할 수 있다.
# 이 경우 문서 순서로 고르면 근거 없이 주석 값을 정답으로 답하게 된다.
# --------------------------------------------------------------------------


def _strip_table_classification(document: dict[str, Any]) -> dict[str, Any]:
    for table in document["tables"]:
        table.pop("table_group_class", None)

    return document


def _set_table_classification(document: dict[str, Any], value: str) -> dict[str, Any]:
    for table in document["tables"]:
        table["table_group_class"] = value

    return document


def test_ambiguous_fixture_candidates_are_indistinguishable_by_old_rule(
    ambiguous_document: dict[str, Any],
) -> None:
    """fixture 전제 확인: 두 후보의 axis_depth와 row_index가 실제로 같아야 한다."""
    contexts = []

    for table in ambiguous_document["tables"]:
        for row_index, row in enumerate(table["rows"]):
            for cell in row:
                if cell.get("acode") != "ifrs-full_Revenue":
                    continue

                context = parse_accounting_context(cell["acontext"])

                contexts.append((context.axis_depth, row_index))

    assert len(contexts) == 2
    assert contexts[0] == contexts[1]


def test_classify_table_maps_parser_metadata_to_internal_class() -> None:
    assert classify_table({"table_group_class": "financial_statement"}) == (
        TABLE_CLASS_PRIMARY_STATEMENT
    )

    assert classify_table({"table_group_class": "note"}) == TABLE_CLASS_NOTE


def test_classify_table_returns_none_without_metadata() -> None:
    """분류 정보가 없으면 판단하지 않는다."""
    assert classify_table({"table_id": "table_0101"}) is None

    assert classify_table({"table_id": "table_0101", "attributes": {}}) is None


def test_classify_table_returns_none_for_unknown_marking() -> None:
    """모르는 표기를 임의로 본표로 보지 않는다."""
    assert classify_table({"table_group_class": "{XBRL}SOMETHING_NEW"}) is None


def test_classify_table_does_not_infer_from_section_or_order(
    ambiguous_document: dict[str, Any],
) -> None:
    """section 제목이 '연결재무제표'라는 이유만으로 본표라고 판단하지 않는다."""
    _strip_table_classification(ambiguous_document)

    for table in ambiguous_document["tables"]:
        assert classify_table(table) is None


def test_table_class_priority_prefers_primary_statement_over_note() -> None:
    assert table_class_priority(TABLE_CLASS_PRIMARY_STATEMENT) < table_class_priority(
        TABLE_CLASS_NOTE
    )

    assert table_class_priority(None) is None


def test_extract_revenue_prefers_primary_statement_over_note_when_classified(
    ambiguous_document: dict[str, Any],
) -> None:
    """Case 1: 표 분류가 있으면 본표를 고른다."""
    fact = extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    assert fact.location.table_id == "table_0101"
    assert fact.raw_value == "2,222,222"
    assert fact.unit_token == "백만원"


def test_extract_revenue_blocks_when_table_classification_missing(
    ambiguous_document: dict[str, Any],
) -> None:
    """Case 2: 분류 정보가 없으면 고르지 않고 막는다."""
    _strip_table_classification(ambiguous_document)

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    assert error.value.reason == "ambiguous_candidates"


def test_extract_revenue_blocks_when_candidates_share_classification(
    ambiguous_document: dict[str, Any],
) -> None:
    """Case 3: 분류가 있어도 우선순위가 같으면 막는다."""
    _set_table_classification(ambiguous_document, "financial_statement")

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    assert error.value.reason == "ambiguous_candidates"


def test_extract_revenue_does_not_fall_back_to_document_order(
    ambiguous_document: dict[str, Any],
) -> None:
    """분류가 없을 때 표 순서를 바꿔도 값을 고르지 않는다(순서 의존 금지)."""
    _strip_table_classification(ambiguous_document)

    ambiguous_document["tables"].reverse()

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    assert error.value.reason == "ambiguous_candidates"


def test_ambiguous_block_detail_reports_provenance_without_values(
    ambiguous_document: dict[str, Any],
) -> None:
    """막힌 이유에 후보 수와 위치가 남되 실제 수치는 노출하지 않는다."""
    _strip_table_classification(ambiguous_document)

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    detail = error.value.detail

    assert "2개" in detail
    assert METRIC_REVENUE in detail
    assert "2025" in detail
    assert SCOPE_CONSOLIDATED in detail

    assert "table_0101" in detail
    assert "table_0102" in detail
    assert "3. 연결재무제표 주석" in detail

    assert "2,222,222" not in detail
    assert "3,333,333" not in detail


def test_extract_revenue_selects_single_candidate_without_classification(
    ambiguous_document: dict[str, Any],
) -> None:
    """Case 4: 후보가 하나뿐이면 분류 정보가 없어도 기존대로 동작한다."""
    _strip_table_classification(ambiguous_document)

    ambiguous_document["tables"] = [
        table
        for table in ambiguous_document["tables"]
        if table["table_id"] == "table_0101"
    ]

    fact = extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    assert fact.location.table_id == "table_0101"
    assert fact.raw_value == "2,222,222"


def test_service_reports_ambiguous_candidates_as_blocked(
    ambiguous_document: dict[str, Any],
    resolved_filing,
) -> None:
    """모호한 후보는 값 대신 BLOCKED로 상위에 전달된다."""
    plan, filing = resolved_filing

    _strip_table_classification(ambiguous_document)

    result = build_result_from_document(
        ambiguous_document, plan=plan, filing=filing
    ).to_dict()

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "extraction"
    assert result["blocked"]["reason"] == "ambiguous_candidates"
    assert result["fact"] is None
    assert result["evidence"] == []


# --------------------------------------------------------------------------
# 8-c. 단위 귀속 (실제 사업보고서에서 발견)
#
# parser의 detect_table_unit은 표 자손 전체에서 '단위'가 든 문자열을 찾는다. 그래서
# 손익계산서의 '기본주당이익 (단위 : 원) ...' 행을 표 단위로 집어오고, 그 값이
# table["unit"]에 들어가 TABLE-GROUP이 선언한 '(단위 : 백만원)'을 가린다.
#
# 실측: 2024 연결 매출액 기준 45개 문서 전부가 table 단위는 신뢰 불가, group 단위는
# 신뢰 가능한 상태였다. 값을 추측하지 않고 그룹 선언까지 따라가면 해소된다.
# --------------------------------------------------------------------------

EPS_ROW_TEXT = "기본주당이익 (단위 : 원) 4,950 2,131 8,057"


def _group_tables(
    document: dict[str, Any],
    *,
    group_unit: str | None,
    aclass: str | None = None,
    group_id: str = "table_group_0001",
) -> dict[str, Any]:
    """flat tables를 TABLE-GROUP 하나로 묶은 parser output 형태로 만든다."""
    for table in document["tables"]:
        table["table_group_id"] = group_id

    document["table_groups"] = [
        {
            "table_group_id": group_id,
            "aclass": aclass,
            "unit": group_unit,
            "tables": document["tables"],
        }
    ]

    return document


def test_targeted_extractor_uses_table_group_unit_when_table_declares_none(
    synthetic_document: dict[str, Any],
) -> None:
    """표에 선언이 없으면 TABLE-GROUP이 선언한 단위를 쓴다."""
    for table in synthetic_document["tables"]:
        table["unit"] = None
        table["own_unit"] = None

    _group_tables(synthetic_document, group_unit="(단위 : 백만원)")

    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert fact.unit_token == "백만원"
    assert fact.unit_multiplier == 1_000_000


def test_targeted_extractor_prefers_group_unit_over_contaminated_table_unit(
    synthetic_document: dict[str, Any],
) -> None:
    """
    표 본문에서 잘못 수집된 단위 문자열보다 TABLE-GROUP 선언을 쓴다.

    오염된 값을 쓰면 x1(원)이 되어 백만 배 작은 값이 나온다. 자릿수까지 확인한다.
    """
    for table in synthetic_document["tables"]:
        table["own_unit"] = EPS_ROW_TEXT
        table["unit"] = EPS_ROW_TEXT

    _group_tables(synthetic_document, group_unit="(단위 : 백만원)")

    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert fact.unit_text == "(단위 : 백만원)"
    assert fact.unit_token == "백만원"
    assert fact.value_in_base_unit == fact.value * 1_000_000


def test_targeted_extractor_still_blocks_when_every_unit_source_is_untrusted(
    synthetic_document: dict[str, Any],
) -> None:
    """그룹 선언도 오염됐으면 신뢰 검사를 우회하지 않고 그대로 막는다."""
    for table in synthetic_document["tables"]:
        table["own_unit"] = EPS_ROW_TEXT
        table["unit"] = EPS_ROW_TEXT

    _group_tables(synthetic_document, group_unit=EPS_ROW_TEXT)

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert error.value.reason == "untrusted_table_unit"


def test_targeted_extractor_blocks_when_no_unit_source_exists(
    synthetic_document: dict[str, Any],
) -> None:
    """선언이 아예 없는 경우는 '오염'이 아니라 '없음'으로 구분해 보고한다."""
    for table in synthetic_document["tables"]:
        table["own_unit"] = None
        table["unit"] = None

    _group_tables(synthetic_document, group_unit=None)

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(synthetic_document, fiscal_year=2025)

    assert error.value.reason == "missing_table_unit"


def test_resolve_table_unit_reports_skipped_untrusted_sources() -> None:
    """건너뛴 선언을 함께 돌려줘야 호출자가 '없음'과 '신뢰 불가'를 구분할 수 있다."""
    table = {"table_id": "table_0001", "own_unit": EPS_ROW_TEXT, "unit": EPS_ROW_TEXT}

    unit, rejected = resolve_table_unit(table, {"unit": "(단위 : 백만원)"})

    assert unit is not None
    assert unit.token == "백만원"
    assert [spec.text for spec in rejected] == [EPS_ROW_TEXT]


def test_resolve_table_unit_does_not_read_rows_or_sentences() -> None:
    """숫자 셀이나 본문 문장에서 단위를 유추하지 않는다."""
    table = {
        "table_id": "table_0001",
        "own_unit": None,
        "unit": None,
        "rows": [[{"text": "매출액"}, {"text": "1,000"}]],
        "raw_context": "금액 단위는 백만원입니다.",
    }

    unit, rejected = resolve_table_unit(table, None)

    assert unit is None
    assert rejected == ()


def test_build_table_group_index_links_flat_tables_to_their_group(
    synthetic_document: dict[str, Any],
) -> None:
    _group_tables(synthetic_document, group_unit="(단위 : 백만원)", aclass="{XBRL}IS_C1")

    index = build_table_group_index(synthetic_document)

    for table in synthetic_document["tables"]:
        assert index[table["table_id"]]["aclass"] == "{XBRL}IS_C1"


# --------------------------------------------------------------------------
# 8-d. ACLASS 기반 본표/주석 판정
#
# 실측 corpus(annual 47종)의 TABLE-GROUP ACLASS는 {XBRL} 이름공간에서 173종이었으나
# 접두 토큰은 다섯 개뿐이었다: IS(6) BS(2) CF(2) EF(2) NT(161).
# 같은 문서에 DART 서식 고유 ACLASS도 89종 함께 있어 이름공간으로 걸러야 한다.
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "aclass",
    ["{XBRL}IS_C1", "{XBRL}IS_S3", "{XBRL}BS_C", "{XBRL}CF_S", "{XBRL}EF_C"],
)
def test_primary_statement_aclass_is_classified_as_primary(aclass: str) -> None:
    """손익계산서·재무상태표·현금흐름표·자본변동표는 본표다."""
    assert classify_table({}, {"aclass": aclass}) == TABLE_CLASS_PRIMARY_STATEMENT


@pytest.mark.parametrize("aclass", ["{XBRL}NT_C_D831150", "{XBRL}NT_S_U800405"])
def test_note_aclass_is_classified_as_note(aclass: str) -> None:
    assert classify_table({}, {"aclass": aclass}) == TABLE_CLASS_NOTE


@pytest.mark.parametrize(
    "aclass",
    [
        "{XBRL}ZZ_C1",  # 모르는 XBRL 표기
        "INC_STAT",  # DART 서식 ACLASS. 이름이 손익계산서처럼 보여도 XBRL 본표가 아니다.
        "SUB_BLC",
        "IAM_TBL1",
        "NORMAL",
        "",
    ],
)
def test_unknown_aclass_is_not_classified(aclass: str) -> None:
    """모르는 표기를 본표로 넘겨짚지 않는다."""
    assert classify_table({}, {"aclass": aclass}) is None


def test_table_own_marking_wins_over_group_aclass() -> None:
    """표에 직접 붙은 표기가 더 구체적인 선언이므로 우선한다."""
    table = {"table_group_class": "note"}

    assert classify_table(table, {"aclass": "{XBRL}IS_C1"}) == TABLE_CLASS_NOTE


def test_classify_table_without_group_keeps_previous_behaviour() -> None:
    """그룹을 넘기지 않으면 기존과 같이 표 표기만 본다."""
    assert classify_table({"table_id": "table_0001"}) is None


def test_extract_revenue_prefers_primary_statement_by_group_aclass(
    ambiguous_document: dict[str, Any],
) -> None:
    """본표와 주석에 같은 후보가 있으면 그룹 ACLASS로 본표를 고른다."""
    _strip_table_classification(ambiguous_document)

    primary, note = ambiguous_document["tables"]

    ambiguous_document["table_groups"] = [
        {
            "table_group_id": "table_group_0001",
            "aclass": "{XBRL}IS_C1",
            "unit": None,
            "tables": [primary],
        },
        {
            "table_group_id": "table_group_0002",
            "aclass": "{XBRL}NT_C_D831150",
            "unit": None,
            "tables": [note],
        },
    ]

    fact = extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    assert fact.location.table_id == "table_0101"
    assert fact.raw_value == "2,222,222"
    assert fact.unit_token == "백만원"


def test_extract_revenue_blocks_when_both_group_aclasses_are_unknown(
    ambiguous_document: dict[str, Any],
) -> None:
    """분류하지 못하는 ACLASS만 있으면 하나를 골라내지 않고 막는다."""
    _strip_table_classification(ambiguous_document)

    primary, note = ambiguous_document["tables"]

    ambiguous_document["table_groups"] = [
        {
            "table_group_id": "table_group_0001",
            "aclass": "INC_STAT",
            "unit": None,
            "tables": [primary],
        },
        {
            "table_group_id": "table_group_0002",
            "aclass": "{XBRL}ZZ_C1",
            "unit": None,
            "tables": [note],
        },
    ]

    with pytest.raises(ExtractionBlockedError) as error:
        extract_revenue_fact(ambiguous_document, fiscal_year=2025)

    assert error.value.reason == "ambiguous_candidates"


# --------------------------------------------------------------------------
# 9. Evidence provenance
# --------------------------------------------------------------------------


@pytest.fixture
def resolved_filing(synthetic_manifest_path: Path, nfd_corpus_root: Path):
    plan = build_query_plan(
        "테스트반도체 2025년 연결 기준 매출액이 얼마야?",
        known_companies=["테스트반도체"],
    )

    return plan, locate_filing(
        manifest_path=synthetic_manifest_path,
        corpus_root=nfd_corpus_root,
        company=plan.company,
        document_class=plan.document_class,
        document_subtype=plan.document_subtype,
        year=plan.year,
    )


def test_evidence_carries_full_provenance_chain(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    _, filing = resolved_filing

    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    evidence = build_evidence(fact, filing=filing, document=synthetic_document)

    assert evidence.doc_id == "periodic_SYNTHETIC0001"
    assert evidence.receipt_no == "SYNTHETIC0001"
    assert evidence.section_id == "section_0002"
    assert evidence.section_path == ("III. 재무에 관한 사항", "2. 연결재무제표")
    assert evidence.table_id == "table_0002"
    assert evidence.row_index == 2
    assert evidence.column_index == 1
    assert evidence.label_text == "수익(매출액)"
    assert evidence.quote == "1,234,567"
    assert evidence.acode == "ifrs-full_Revenue"
    assert evidence.acontext.startswith("CFY2025dFY")
    assert evidence.unit_text == "(단위 : 백만원)"
    assert evidence.source_path


def test_evidence_id_is_deterministic(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    _, filing = resolved_filing

    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    first = build_evidence(fact, filing=filing, document=synthetic_document)
    second = build_evidence(fact, filing=filing, document=synthetic_document)

    assert first.evidence_id == second.evidence_id
    assert first.evidence_id.startswith("evidence_periodic_SYNTHETIC0001_table_0002")


# --------------------------------------------------------------------------
# 10. 구조화 결과 조립 / 오케스트레이션
# --------------------------------------------------------------------------


def test_structured_result_shape(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    plan, filing = resolved_filing

    fact = extract_revenue_fact(synthetic_document, fiscal_year=2025)

    evidence = build_evidence(fact, filing=filing, document=synthetic_document)

    result = build_structured_result(plan, filing=filing, fact=fact, evidence=evidence)

    assert result["status"] == STATUS_OK
    assert result["query"]["metric"] == METRIC_REVENUE
    assert result["fact"]["value"] == "1234567"
    assert result["fact"]["unit_token"] == "백만원"
    assert result["fact"]["value_in_base_unit"] == "1234567000000"
    assert len(result["evidence"]) == 1
    assert result["calculations"] == []

    # 자연어 문장을 만들지 않는다.
    assert "answer" not in result


def test_service_orchestration_returns_ok_on_synthetic_document(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    plan, filing = resolved_filing

    result = build_result_from_document(
        synthetic_document, plan=plan, filing=filing
    ).to_dict()

    assert result["status"] == STATUS_OK
    assert result["fact"]["accounting_scope"] == SCOPE_CONSOLIDATED
    assert result["fact"]["fiscal_year"] == 2025
    assert result["evidence"][0]["table_id"] == "table_0002"


def test_service_orchestration_reports_blocked_without_acontext(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    """parser가 acontext를 주지 않으면 값 대신 BLOCKED를 돌려준다."""
    plan, filing = resolved_filing

    for table in synthetic_document["tables"]:
        for row in table["rows"]:
            for cell in row:
                cell.pop("acontext", None)

    result = build_result_from_document(
        synthetic_document, plan=plan, filing=filing
    ).to_dict()

    assert result["status"] == STATUS_BLOCKED
    assert result["blocked"]["stage"] == "extraction"
    assert result["blocked"]["reason"] == "missing_acontext"
    assert result["fact"] is None
    assert result["evidence"] == []
    assert result["filing"]["doc_id"] == "periodic_SYNTHETIC0001"


def test_service_result_is_json_serializable(
    synthetic_document: dict[str, Any],
    resolved_filing,
) -> None:
    import json

    plan, filing = resolved_filing

    result = build_result_from_document(
        synthetic_document, plan=plan, filing=filing
    ).to_dict()

    assert json.loads(json.dumps(result, ensure_ascii=False))["status"] == STATUS_OK
