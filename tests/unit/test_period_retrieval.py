"""기간별 원천 Fact 확보를 검증한다.

무엇을 고정하는가:
    시계열 비교 질문은 요청한 회계연도마다 값을 하나씩 확보해야 한다. 그 확보 흐름이 Pattern
    A와 같은 경로를 쓰는지, 그리고 하나라도 못 구했을 때 나머지만 가지고 진행할 수 없는지를
    본다.

무엇을 검증하지 않는가:
    계산하지 않는다. 두 기간 값을 비교해도 되는지도 보지 않는다. 그 판정은 Pattern C
    handler와 calculator의 몫이고 아직 구현되지 않았다. 이 파일이 고정하는 것은 "요청받은
    기간·지표를 전부 확보하거나 하나도 쓰지 않는다"까지다.

Router와의 관계:
    이 모듈은 Router에 등록되지 않는다. 시계열 비교 질문은 여전히
    routing/unimplemented_intent로 막힌다. 그 사실은 test_multi_period_guard.py가 고정한다.
"""

from __future__ import annotations

import copy
import os
import re
from pathlib import Path
from typing import Any

import pytest

from disclosure_agent.agent import period_retrieval
from disclosure_agent.agent.answer_builder import (
    STAGE_EXTRACTION,
    STAGE_FILING_DISCOVERY,
)
from disclosure_agent.agent.period_retrieval import (
    collect_period_facts,
    fetch_period_fact,
)
from disclosure_agent.common.enums import FactAccountingScope, MetricConcept

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"

DEFAULT_CORPUS_ROOT = (
    Path(__file__).resolve().parents[2] / "data" / "raw" / "3.공시" / "3.공시" / "corpus"
)

# 합성 manifest가 담고 있는 기업과 연도. conftest의 SYNTHETIC_MANIFEST_RECORDS와 같아야 한다.
SYNTHETIC_COMPANY = "테스트반도체"

SYNTHETIC_YEARS = (2024, 2025)

# 실측 코퍼스 질문 대상. Q08 원문이 묻는 기업이다.
REAL_COMPANY = "한미반도체"


@pytest.fixture
def stub_documents(
    monkeypatch: pytest.MonkeyPatch,
    synthetic_document: dict[str, Any],
):
    """
    parser 호출을 합성 문서로 대체하되, 공시마다 다른 문서를 돌려준다.

    합성 corpus의 XML은 파싱 경계를 시험하기 위한 최소 파일이므로 정상 흐름 검증에는 쓸 수
    없다. 그렇다고 모든 연도에 같은 문서를 돌려주면 "연도마다 그 해의 보고서를 연다"는 성질이
    검사에서 사라진다. 그래서 경로에서 접수번호를 읽어 metadata.doc_id를 그 공시의 것으로
    바꿔 돌려준다.

    합성 문서 자체는 CFY2025와 PFY2024 컨텍스트를 모두 담고 있어 두 연도 모두 추출된다.
    """
    receipt_pattern = re.compile(r"(SYNTHETIC\d+)")

    def _load(document_path: Any) -> dict[str, Any]:
        document = copy.deepcopy(synthetic_document)

        match = receipt_pattern.search(str(document_path))

        if match is not None:
            receipt_no = match.group(1)
            metadata = document.setdefault("metadata", {})
            metadata["doc_id"] = f"periodic_{receipt_no}"
            metadata["receipt_no"] = receipt_no
            metadata["source_path"] = str(document_path)

        return document

    monkeypatch.setattr(period_retrieval, "load_parsed_document", _load)


def _resolve_corpus_root() -> Path | None:
    raw = os.environ.get(CORPUS_ROOT_ENV)

    candidate = Path(raw) if raw else DEFAULT_CORPUS_ROOT

    if candidate.is_dir() and (candidate / "manifest.jsonl").is_file():
        return candidate

    return None


def _collect(
    *,
    corpus_root: Path,
    manifest_path: Path | None = None,
    company: str = SYNTHETIC_COMPANY,
    fiscal_years: tuple[int, ...] = SYNTHETIC_YEARS,
    metrics: tuple[str, ...] = (MetricConcept.REVENUE,),
) -> period_retrieval.PeriodFactCollection:
    """이 파일이 반복해 쓰는 호출 형태를 한곳에 둔다."""
    return collect_period_facts(
        company=company,
        fiscal_years=fiscal_years,
        metrics=metrics,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        document_class="periodic",
        document_subtype="annual",
        corpus_root=corpus_root,
        manifest_path=manifest_path,
    )


# --------------------------------------------------------------------------
# 1. 두 기간 x 한 지표
# --------------------------------------------------------------------------


def test_two_periods_one_metric_collects_one_fact_per_period(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    stub_documents,  # noqa: ARG001 - 파싱 대체를 설치한다
) -> None:
    """
    요청한 연도마다 Fact가 하나씩 나온다.

    연도별로 서로 다른 값을 확보했는지가 요지다. 한 연도의 값을 두 번 쓰거나 첫 연도만 읽고
    끝내면 시계열 비교의 입력이 되지 못한다.
    """

    collection = _collect(
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert collection.is_complete
    assert len(collection.facts) == 2
    assert {fact.fiscal_year for fact in collection.facts} == set(SYNTHETIC_YEARS)
    assert {fact.metric for fact in collection.facts} == {MetricConcept.REVENUE}
    assert {fact.company for fact in collection.facts} == {SYNTHETIC_COMPANY}


def test_collected_facts_keep_the_requested_order(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    stub_documents,  # noqa: ARG001 - 파싱 대체를 설치한다
) -> None:
    """
    요청 순서를 지킨다. 정렬하지 않는다.

    받는 쪽이 요청과 결과를 나란히 놓고 대조할 수 있어야 한다.
    """

    collection = _collect(
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
        fiscal_years=(2025, 2024),
    )

    assert [fact.fiscal_year for fact in collection.facts] == [2025, 2024]


# --------------------------------------------------------------------------
# 2. Evidence 무결성
# --------------------------------------------------------------------------


def test_every_fact_evidence_reference_resolves(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    stub_documents,  # noqa: ARG001 - 파싱 대체를 설치한다
) -> None:
    """
    Fact가 가리키는 Evidence가 실제로 함께 나와야 한다.

    해소되지 않는 참조가 남으면 받는 쪽이 근거를 확인할 수 없고, 평가의 무결성 검사
    (integrity.fact_evidence_references)도 통과하지 못한다.
    """

    collection = _collect(
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    available = {item.evidence_id for item in collection.evidence}

    dangling = [
        evidence_id
        for fact in collection.facts
        for evidence_id in fact.evidence_ids
        if evidence_id not in available
    ]

    assert dangling == []
    assert len(available) == len(collection.evidence)


def test_facts_and_evidence_are_distinct_per_period_and_metric(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    stub_documents,  # noqa: ARG001 - 파싱 대체를 설치한다
) -> None:
    """
    기간마다 서로 다른 식별자를 가져야 한다.

    같은 식별자가 겹치면 두 기간의 값이 하나로 뭉개져 비교할 것이 남지 않는다.
    """

    collection = _collect(
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    fact_ids = [fact.fact_id for fact in collection.facts]
    evidence_ids = [item.evidence_id for item in collection.evidence]

    assert len(set(fact_ids)) == len(fact_ids)
    assert len(set(evidence_ids)) == len(evidence_ids)


# --------------------------------------------------------------------------
# 3. 부분 성공 금지
# --------------------------------------------------------------------------


def test_missing_period_does_not_return_the_other_period(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    stub_documents,  # noqa: ARG001 - 파싱 대체를 설치한다
) -> None:
    """
    한 기간을 확보하지 못하면 나머지 기간도 쓰지 않는다.

    이 파일에서 가장 중요한 검사다. 2024년만 담은 결과를 돌려주면 받는 쪽이 그것으로 답을
    조립할 수 있고, 그러면 두 기간을 물은 질문에 한 기간만 답하는 결과가 된다. 그것이
    F6-02에서 막은 동작이다.

    2019년은 합성 manifest에 없다.
    """

    collection = _collect(
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
        fiscal_years=(2025, 2019),
    )

    assert not collection.is_complete
    assert collection.facts == ()
    assert collection.evidence == ()
    assert collection.period_facts == ()

    blocked = collection.blocked

    assert blocked is not None
    assert blocked.stage == STAGE_FILING_DISCOVERY
    assert blocked.reason == "no_matching_filing"

    # 어느 조합에서 막혔는지가 남아야 후속 작업을 고를 수 있다.
    assert "2019" in blocked.detail


def test_missing_metric_does_not_return_the_other_metric(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    stub_documents,  # noqa: ARG001 - 파싱 대체를 설치한다
) -> None:
    """
    한 지표를 확보하지 못하면 같은 연도의 다른 지표도 쓰지 않는다.

    Q08은 연도마다 분자와 분모가 모두 있어야 한다. 한쪽만 있는 결과는 계산 입력이 되지
    못하며, 그것을 성공으로 돌려주면 받는 쪽이 남은 값으로 무언가를 시도하게 된다.
    """

    collection = _collect(
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
        metrics=(MetricConcept.REVENUE, MetricConcept.HOLDING_SHARES),
    )

    assert not collection.is_complete
    assert collection.facts == ()
    assert collection.evidence == ()

    blocked = collection.blocked

    assert blocked is not None
    assert blocked.stage == STAGE_EXTRACTION

    assert MetricConcept.HOLDING_SHARES.value in blocked.detail


@pytest.mark.parametrize(
    ("fiscal_years", "metrics"),
    [
        ((), (MetricConcept.REVENUE,)),
        (SYNTHETIC_YEARS, ()),
    ],
)
def test_empty_request_is_rejected(
    fiscal_years: tuple[int, ...],
    metrics: tuple[str, ...],
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    빈 요청은 조용히 빈 성공으로 돌려주지 않는다.

    아무것도 요청하지 않았는데 is_complete가 True면 받는 쪽이 "전부 확보했다"로 읽는다.
    """
    with pytest.raises(ValueError):
        _collect(
            corpus_root=nfd_corpus_root,
            manifest_path=synthetic_manifest_path,
            fiscal_years=fiscal_years,
            metrics=metrics,
        )


# --------------------------------------------------------------------------
# 4. 단일 조합 primitive
# --------------------------------------------------------------------------


def test_fetch_period_fact_reports_the_stop_point(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
) -> None:
    """
    primitive는 예외를 올리지 않고 막힌 지점을 남긴다.

    Pattern A가 이미 이 규약에 기대고 있다. 어느 단계에서 왜 막혔는지를 호출자가 그대로
    보고 답변 가능성을 판정한다.
    """
    found = fetch_period_fact(
        company=SYNTHETIC_COMPANY,
        fiscal_year=2019,
        metric=MetricConcept.REVENUE,
        accounting_scope=FactAccountingScope.CONSOLIDATED,
        document_class="periodic",
        document_subtype="annual",
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
    )

    assert not found.is_found
    assert found.fact is None
    assert found.evidence is None

    assert found.blocked is not None
    assert found.blocked.stage == STAGE_FILING_DISCOVERY


# --------------------------------------------------------------------------
# 5. C-1 불변식 — 다중 기간 경로가 scalar 통로를 쓰지 않는다
# --------------------------------------------------------------------------


def test_collection_path_never_reads_the_scalar_year_property(
    synthetic_manifest_path: Path,
    nfd_corpus_root: Path,
    stub_documents,  # noqa: ARG001 - 파싱 대체를 설치한다
) -> None:
    """
    다중 기간 확보 경로는 QueryPlan.year를 읽지 않는다.

    C-1이 만든 성질을 그대로 증거로 쓴다. 시계열 비교 계획에서 year를 읽으면 ValueError가
    나므로, 그 계획을 들고 확보가 끝까지 성공했다면 그 통로를 읽지 않았다는 뜻이다.

    이 모듈이 애초에 QueryPlan을 받지 않는다는 점도 함께 고정한다. 계획을 받지 않으면
    scalar 통로에 손이 닿을 수 없다.
    """
    from disclosure_agent.agent.query_analyzer import build_query_plan

    plan = build_query_plan(
        "테스트반도체 2024년 매출과 2025년 매출 비교해줘",
        known_companies=(SYNTHETIC_COMPANY,),
    )

    # 전제 확인: 이 계획에서 scalar 통로는 막혀 있다.
    with pytest.raises(ValueError):
        plan.year  # noqa: B018 - 접근 자체가 검사 대상이다


    collection = _collect(
        corpus_root=nfd_corpus_root,
        manifest_path=synthetic_manifest_path,
        fiscal_years=plan.years,
    )

    assert collection.is_complete
    assert {fact.fiscal_year for fact in collection.facts} == set(plan.years)


# --------------------------------------------------------------------------
# 6. 실측 코퍼스 — 두 기간 x 두 지표 — Q08이 요구하는 네 Fact
# --------------------------------------------------------------------------


@pytest.mark.corpus
def test_q08_source_facts_are_available_in_the_actual_corpus() -> None:
    """
    Q08 원문이 요구하는 원천 Fact 네 건이 실측 코퍼스에 있는지 본다.

    계산은 하지 않는다. 영업이익률을 만들려면 연도마다 분자와 분모가 필요하고, 그 네 건을
    확보할 수 있는지가 Pattern C 착수의 전제다.
    """
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    collection = _collect(
        corpus_root=corpus_root,
        company=REAL_COMPANY,
        fiscal_years=(2024, 2025),
        metrics=(MetricConcept.REVENUE, MetricConcept.OPERATING_INCOME),
    )

    assert collection.is_complete
    assert len(collection.facts) == 4

    # 요청한 (회계연도, 지표) 조합 넷이 그대로 돌아왔는지를 집합 동등성으로 본다. 넷 중
    # 하나라도 다른 연도나 다른 지표로 바뀌면 여기서 걸린다.
    combinations = {(fact.fiscal_year, fact.metric) for fact in collection.facts}

    assert combinations == {
        (year, metric)
        for year in (2024, 2025)
        for metric in (MetricConcept.REVENUE, MetricConcept.OPERATING_INCOME)
    }

    # 근거가 함께 나오고 그 참조가 해소되는지만 본다. 어느 공시에서 왔는지는 고정하지 않는다.
    #
    # 현재 구현은 회계연도마다 locate_filing을 따로 부르므로 연도별로 다른 보고서를 고른다.
    # 다만 각 연도를 그 해의 원 보고서에서 읽을지 이후 보고서의 비교 열에서 읽을지는 D4에서
    # 정할 사항이라, 테스트가 지금의 선택을 정책으로 못박지 않는다.
    available = {item.evidence_id for item in collection.evidence}

    dangling = [
        evidence_id
        for fact in collection.facts
        for evidence_id in fact.evidence_ids
        if evidence_id not in available
    ]

    assert dangling == []

    for fact in collection.facts:
        assert fact.accounting_scope is FactAccountingScope.CONSOLIDATED
        assert fact.normalized_unit == "원"
        assert fact.normalized_value is not None
        assert fact.evidence_ids


@pytest.mark.corpus
def test_actual_corpus_collection_is_atomic() -> None:
    """실측 코퍼스에서도 한 기간을 못 구하면 나머지를 돌려주지 않는다."""
    corpus_root = _resolve_corpus_root()

    if corpus_root is None:
        pytest.skip(f"실측 코퍼스가 없습니다. {CORPUS_ROOT_ENV}로 경로를 지정할 수 있습니다.")

    collection = _collect(
        corpus_root=corpus_root,
        company=REAL_COMPANY,
        # 2026년 사업보고서는 코퍼스 보유 기간 밖이다.
        fiscal_years=(2025, 2026),
        metrics=(MetricConcept.REVENUE,),
    )

    assert not collection.is_complete
    assert collection.facts == ()
    assert collection.evidence == ()
