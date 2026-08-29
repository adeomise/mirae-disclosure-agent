"""corpus 회귀 측정 스크립트의 단위 테스트.

이 테스트는 실측 코퍼스를 필요로 하지 않는다. fixture 해석과 기대 대조는 순수 함수이므로
코퍼스 없이 전부 시험할 수 있고, 그래야 코퍼스가 없는 환경에서도 도구의 고장을 잡는다.

실제 공시 값·접수번호는 쓰지 않는다. 합성 식별자만 사용한다.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

from disclosure_agent.common.enums import (
    AnswerabilityReason,
    AnswerabilityStatus,
    EventLinkRelation,
    EventLinkResolutionStatus,
    EvidenceLocationType,
    ExecutionStatus,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    AgentExecutionResult,
    EventHistory,
    EventLink,
    Evidence,
    ExecutionIssue,
    Fact,
    FieldChange,
    RawReference,
)

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPOSITORY_ROOT / "scripts" / "run_corpus_regression.py"
SHIPPED_CASES_PATH = REPOSITORY_ROOT / "scripts" / "corpus_regression_cases.jsonl"


def _load_script() -> ModuleType:
    """
    스크립트를 경로로 직접 읽어 모듈로 만든다.

    scripts/를 import 경로에 넣지 않기 위해서다. 측정 도구 하나를 시험하려고 저장소의
    import 경로를 바꾸면 다른 테스트의 해석까지 달라진다.
    """
    name = "run_corpus_regression"

    spec = importlib.util.spec_from_file_location(name, SCRIPT_PATH)

    assert spec is not None and spec.loader is not None

    module = importlib.util.module_from_spec(spec)

    # dataclass가 자기 모듈의 namespace를 sys.modules에서 되찾으므로 실행 전에 등록한다.
    sys.modules[name] = module

    spec.loader.exec_module(module)

    return module


harness = _load_script()


# --------------------------------------------------------------------------
# 결과 fixture (합성)
# --------------------------------------------------------------------------


def _single_fact_result(
    *,
    accounting_scope: FactAccountingScope = FactAccountingScope.CONSOLIDATED,
) -> AgentExecutionResult:
    evidence = Evidence(
        evidence_id="evidence_synthetic_0001",
        doc_id="periodic_synthetic_0001",
        location_type=EvidenceLocationType.TABLE,
    )

    fact = Fact(
        fact_id="fact_synthetic_0001",
        company="테스트반도체",
        metric=MetricConcept.REVENUE,
        fiscal_year=2025,
        accounting_scope=accounting_scope,
        evidence_ids=[evidence.evidence_id],
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=[fact],
        evidence=[evidence],
    )


def _multi_fact_result(*scopes: FactAccountingScope) -> AgentExecutionResult:
    """회계 기준이 서로 다른 Fact 여러 건이 함께 돌아온 결과."""
    evidence = Evidence(
        evidence_id="evidence_synthetic_0001",
        doc_id="periodic_synthetic_0001",
        location_type=EvidenceLocationType.TABLE,
    )

    facts = [
        Fact(
            fact_id=f"fact_synthetic_{index:04d}",
            company="테스트반도체",
            metric=MetricConcept.REVENUE,
            fiscal_year=2025,
            accounting_scope=scope,
            evidence_ids=[evidence.evidence_id],
        )
        for index, scope in enumerate(scopes, start=1)
    ]

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        facts=facts,
        evidence=[evidence],
    )


def _event_history_result(*, chain_complete: bool = True) -> AgentExecutionResult:
    evidence = [
        Evidence(
            evidence_id=f"evidence_synthetic_{index}",
            doc_id=f"event_synthetic_{index}",
            location_type=EvidenceLocationType.EVENT_FIELD,
        )
        for index in (1, 2)
    ]

    links = [
        EventLink(
            link_id=f"link_synthetic_{index}",
            source_doc_id="event_synthetic_2",
            relation=EventLinkRelation.CORRECTION_OF,
            raw_reference=RawReference(document_title="합성 공급계약 공시"),
            resolved_doc_id="event_synthetic_1",
            resolution_status=EventLinkResolutionStatus.RESOLVED,
        )
        for index in (1, 2)
    ]

    history = EventHistory(
        event_id="event_supply_contract_event_synthetic_1",
        document_ids=["event_synthetic_1", "event_synthetic_2"],
        changes=[
            FieldChange(
                field_name="contract_amount",
                before_evidence_id="evidence_synthetic_1",
                after_evidence_id="evidence_synthetic_2",
            )
        ],
        chain_complete=chain_complete,
    )

    return AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.ANSWERABLE,
        event_histories=[history],
        event_links=links,
        evidence=evidence,
    )


def _write_cases(tmp_path: Path, *records: dict[str, object]) -> Path:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        "\n".join(json.dumps(record, ensure_ascii=False) for record in records),
        encoding="utf-8",
    )

    return path


VALID_CASE: dict[str, object] = {
    "id": "SYNTH-01",
    "question": "테스트반도체 2025년 연결 기준 매출액이 얼마야?",
    "expect": {"execution_status": "COMPLETED", "facts": 1},
}


# --------------------------------------------------------------------------
# 1. fixture 해석
# --------------------------------------------------------------------------


def test_loads_valid_case_fixture(tmp_path: Path) -> None:
    cases = harness.load_cases(_write_cases(tmp_path, VALID_CASE))

    assert len(cases) == 1
    assert cases[0].case_id == "SYNTH-01"
    assert cases[0].expect == {"execution_status": "COMPLETED", "facts": 1}


def test_blank_lines_are_skipped(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        f"\n{json.dumps(VALID_CASE, ensure_ascii=False)}\n\n",
        encoding="utf-8",
    )

    assert len(harness.load_cases(path)) == 1


def test_missing_fixture_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(harness.RegressionConfigError) as error:
        harness.load_cases(tmp_path / "absent.jsonl")

    assert "not found" in str(error.value)


def test_malformed_json_reports_the_line_number(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text(
        f"{json.dumps(VALID_CASE, ensure_ascii=False)}\n{{not json}}\n",
        encoding="utf-8",
    )

    with pytest.raises(harness.RegressionConfigError) as error:
        harness.load_cases(path)

    assert ":2:" in str(error.value)


def test_case_without_question_is_rejected(tmp_path: Path) -> None:
    path = _write_cases(tmp_path, {"id": "SYNTH-01", "expect": {"facts": 1}})

    with pytest.raises(harness.RegressionConfigError) as error:
        harness.load_cases(path)

    assert "question" in str(error.value)


def test_case_without_expectations_is_rejected(tmp_path: Path) -> None:
    path = _write_cases(tmp_path, {"id": "SYNTH-01", "question": "질문", "expect": {}})

    with pytest.raises(harness.RegressionConfigError) as error:
        harness.load_cases(path)

    assert "expect" in str(error.value)


def test_unknown_expectation_key_is_rejected(tmp_path: Path) -> None:
    """오타 난 기대 항목을 조용히 넘기면 PASS가 아무것도 보증하지 못한다."""
    path = _write_cases(
        tmp_path,
        {"id": "SYNTH-01", "question": "질문", "expect": {"exec_status": "COMPLETED"}},
    )

    with pytest.raises(harness.RegressionConfigError) as error:
        harness.load_cases(path)

    assert "exec_status" in str(error.value)


def test_fact_accounting_scope_is_a_supported_expectation_key(tmp_path: Path) -> None:
    """새 기대 항목이 검증기의 허용 목록에 실제로 들어 있어야 fixture에 쓸 수 있다."""
    path = _write_cases(
        tmp_path,
        {
            "id": "SYNTH-01",
            "question": "질문",
            "expect": {"facts": 1, "fact_accounting_scope": "separate"},
        },
    )

    cases = harness.load_cases(path)

    assert cases[0].expect["fact_accounting_scope"] == "separate"


def test_duplicate_case_id_is_rejected(tmp_path: Path) -> None:
    path = _write_cases(tmp_path, VALID_CASE, VALID_CASE)

    with pytest.raises(harness.RegressionConfigError) as error:
        harness.load_cases(path)

    assert "duplicate" in str(error.value)


def test_empty_fixture_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text("\n\n", encoding="utf-8")

    with pytest.raises(harness.RegressionConfigError):
        harness.load_cases(path)


def test_shipped_fixture_is_valid() -> None:
    """저장소에 넣은 기본 fixture 자체가 형식을 만족해야 한다."""
    cases = harness.load_cases(SHIPPED_CASES_PATH)
    ids = [case.case_id for case in cases]

    assert "Q01" in ids
    assert "Q13" in ids


def test_shipped_q01_cases_pin_the_accounting_scope() -> None:
    """
    Q01 계열은 연결·별도를 각각 고정해야 한다.

    scope 기대를 빼면 '별도 기준' 질문에 연결 Fact가 돌아와도 facts=1로 통과한다.
    연결 케이스까지 함께 고정해야 '항상 연결을 고른다'는 회귀도 잡힌다.
    """
    by_id = {case.case_id: case for case in harness.load_cases(SHIPPED_CASES_PATH)}

    assert by_id["Q01"].expect["fact_accounting_scope"] == "consolidated"
    assert by_id["Q01-FY2024"].expect["fact_accounting_scope"] == "consolidated"
    assert by_id["Q01-SEPARATE"].expect["fact_accounting_scope"] == "separate"


# --------------------------------------------------------------------------
# 2. 기대 대조
# --------------------------------------------------------------------------


def test_matching_single_fact_expectations_pass() -> None:
    failures = harness.check_expectations(
        {
            "execution_status": "COMPLETED",
            "answerability_status": "ANSWERABLE",
            "answerability_reason": None,
            "facts": 1,
            "min_evidence": 1,
            "evidence_linkage": True,
        },
        _single_fact_result(),
    )

    assert failures == []


def test_matching_event_history_expectations_pass() -> None:
    failures = harness.check_expectations(
        {
            "execution_status": "COMPLETED",
            "answerability_status": "ANSWERABLE",
            "event_histories": 1,
            "chain_complete": True,
            "min_event_links": 2,
            "min_evidence": 1,
            "evidence_linkage": True,
        },
        _event_history_result(),
    )

    assert failures == []


def test_all_mismatches_are_reported_not_just_the_first() -> None:
    failures = harness.check_expectations(
        {"execution_status": "BLOCKED", "facts": 3, "min_evidence": 5},
        _single_fact_result(),
    )

    assert len(failures) == 3


def test_status_enum_is_compared_by_value() -> None:
    """결과의 StrEnum과 fixture의 문자열이 같은 값이면 통과해야 한다."""
    result = _single_fact_result()

    assert result.execution_status is ExecutionStatus.COMPLETED
    assert harness.check_expectations({"execution_status": "COMPLETED"}, result) == []


def test_incomplete_chain_is_detected() -> None:
    failures = harness.check_expectations(
        {"chain_complete": True},
        _event_history_result(chain_complete=False),
    )

    assert failures and "chain_complete" in failures[0]


def test_chain_expectation_without_history_fails() -> None:
    failures = harness.check_expectations({"chain_complete": True}, _single_fact_result())

    assert failures and "expected an event history" in failures[0]


def test_blocked_result_matches_issue_expectations() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.BLOCKED,
        execution_issue=ExecutionIssue(
            stage="query_plan",
            reason="unknown_company",
            detail="합성 사유",
        ),
    )

    failures = harness.check_expectations(
        {
            "execution_status": "BLOCKED",
            "answerability_status": None,
            "issue_stage": "query_plan",
            "issue_reason": "unknown_company",
            "facts": 0,
            "evidence": 0,
        },
        result,
    )

    assert failures == []


def test_issue_expectation_on_completed_result_fails() -> None:
    failures = harness.check_expectations(
        {"issue_stage": "query_plan"},
        _single_fact_result(),
    )

    assert failures and "issue_stage" in failures[0]


# --------------------------------------------------------------------------
# 2-1. 회계 기준(연결 / 별도)
# --------------------------------------------------------------------------


def test_consolidated_expectation_matches_a_consolidated_fact() -> None:
    failures = harness.check_expectations(
        {"facts": 1, "fact_accounting_scope": "consolidated"},
        _single_fact_result(accounting_scope=FactAccountingScope.CONSOLIDATED),
    )

    assert failures == []


def test_separate_expectation_matches_a_separate_fact() -> None:
    failures = harness.check_expectations(
        {"facts": 1, "fact_accounting_scope": "separate"},
        _single_fact_result(accounting_scope=FactAccountingScope.SEPARATE),
    )

    assert failures == []


def test_separate_expectation_rejects_a_consolidated_fact() -> None:
    """
    Q01 계열의 주된 실패 모드다.

    '별도 기준' 질문에 연결 Fact 1건이 돌아오면 건수 검사는 통과하므로, scope 검사가
    없으면 이 회귀가 PASS로 지나간다.
    """
    result = _single_fact_result(accounting_scope=FactAccountingScope.CONSOLIDATED)

    assert harness.check_expectations({"facts": 1}, result) == []

    failures = harness.check_expectations(
        {"facts": 1, "fact_accounting_scope": "separate"},
        result,
    )

    assert failures
    assert "fact_accounting_scope" in failures[0]
    assert "'consolidated'" in failures[0]


def test_scope_expectation_without_any_fact_fails() -> None:
    failures = harness.check_expectations(
        {"fact_accounting_scope": "separate"},
        AgentExecutionResult(
            execution_status=ExecutionStatus.COMPLETED,
            answerability_status=AnswerabilityStatus.ANSWERABLE,
        ),
    )

    assert failures and "got none" in failures[0]


def test_every_fact_must_match_the_expected_scope() -> None:
    """첫 Fact만 보면 뒤에 섞여 들어온 다른 기준을 놓친다."""
    failures = harness.check_expectations(
        {"fact_accounting_scope": "separate"},
        _multi_fact_result(FactAccountingScope.SEPARATE, FactAccountingScope.CONSOLIDATED),
    )

    assert len(failures) == 1
    assert "fact_synthetic_0002" in failures[0]


def test_scope_is_compared_by_enum_value() -> None:
    """결과의 StrEnum과 fixture의 소문자 문자열이 같은 값이면 통과해야 한다."""
    result = _single_fact_result(accounting_scope=FactAccountingScope.SEPARATE)

    assert result.facts[0].accounting_scope is FactAccountingScope.SEPARATE
    assert harness.check_expectations({"fact_accounting_scope": "separate"}, result) == []


# --------------------------------------------------------------------------
# 3. Evidence 연결
# --------------------------------------------------------------------------


def test_dangling_fact_evidence_reference_is_detected() -> None:
    result = _single_fact_result()
    result.facts[0].evidence_ids = ["evidence_that_is_not_here"]

    failures = harness.check_expectations({"evidence_linkage": True}, result)

    assert failures and "unknown evidence" in failures[0]


def test_fact_without_any_evidence_reference_is_detected() -> None:
    result = _single_fact_result()
    result.facts[0].evidence_ids = []

    failures = harness.check_expectations({"evidence_linkage": True}, result)

    assert failures and "no evidence_ids" in failures[0]


def test_dangling_field_change_evidence_reference_is_detected() -> None:
    result = _event_history_result()
    result.event_histories[0].changes[0].after_evidence_id = "evidence_that_is_not_here"

    failures = harness.check_expectations({"evidence_linkage": True}, result)

    assert failures and "unknown evidence" in failures[0]


def test_evidence_count_alone_does_not_prove_linkage() -> None:
    """건수만 세는 검사로는 연결이 끊긴 결과를 통과시킨다는 것을 고정한다."""
    result = _single_fact_result()
    result.facts[0].evidence_ids = ["evidence_that_is_not_here"]

    assert harness.check_expectations({"min_evidence": 1}, result) == []
    assert harness.check_expectations({"min_evidence": 1, "evidence_linkage": True}, result)


# --------------------------------------------------------------------------
# 4. 결과 요약
# --------------------------------------------------------------------------


def test_summary_is_json_serializable_and_holds_no_disclosure_values() -> None:
    summary = harness.summarize_result(_single_fact_result())

    json.dumps(summary)

    assert summary["execution_status"] == "COMPLETED"
    assert summary["facts"] == 1
    # 요약은 건수와 상태만 담는다. 실제 공시 값이나 식별자를 싣지 않는다.
    assert set(summary) == {
        "execution_status",
        "answerability_status",
        "answerability_reason",
        "issue_stage",
        "issue_reason",
        "facts",
        "fact_accounting_scope",
        "evidence",
        "event_links",
        "event_histories",
        "chain_complete",
        "calculations",
        "comparisons",
        "calculation_operations",
        "calculation_units",
        "warnings",
        "conversion_gaps",
        "conversion_gaps_pending",
    }


def test_summary_reports_the_selected_accounting_scope() -> None:
    """FAIL을 볼 때 어떤 기준이 선택됐는지 결과만으로 알 수 있어야 한다."""
    consolidated = harness.summarize_result(
        _single_fact_result(accounting_scope=FactAccountingScope.CONSOLIDATED)
    )
    separate = harness.summarize_result(
        _single_fact_result(accounting_scope=FactAccountingScope.SEPARATE)
    )

    assert consolidated["fact_accounting_scope"] == ["consolidated"]
    assert separate["fact_accounting_scope"] == ["separate"]

    mixed = harness.summarize_result(
        _multi_fact_result(FactAccountingScope.SEPARATE, FactAccountingScope.CONSOLIDATED)
    )

    assert mixed["fact_accounting_scope"] == ["consolidated", "separate"]

    # 분류만 담는다. 실제 공시 수치는 요약에 들어가지 않는다.
    json.dumps(mixed)


def test_summary_scope_is_empty_when_there_is_no_fact() -> None:
    summary = harness.summarize_result(
        AgentExecutionResult(execution_status=ExecutionStatus.BLOCKED)
    )

    assert summary["fact_accounting_scope"] == []


def test_summary_reports_answerability_reason_value() -> None:
    result = AgentExecutionResult(
        execution_status=ExecutionStatus.COMPLETED,
        answerability_status=AnswerabilityStatus.PARTIALLY_ANSWERABLE,
        answerability_reason=AnswerabilityReason.INCOMPLETE_CHAIN,
    )

    summary = harness.summarize_result(result)

    assert summary["answerability_status"] == "PARTIALLY_ANSWERABLE"
    assert summary["answerability_reason"] == "incomplete_chain"


# --------------------------------------------------------------------------
# 5. 경로 해석과 CLI
# --------------------------------------------------------------------------


def test_missing_corpus_root_is_a_config_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(harness.CORPUS_ROOT_ENV, raising=False)

    with pytest.raises(harness.RegressionConfigError) as error:
        harness.resolve_corpus_root(None)

    assert harness.CORPUS_ROOT_ENV in str(error.value)


def test_corpus_root_falls_back_to_environment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(harness.CORPUS_ROOT_ENV, str(tmp_path))

    assert harness.resolve_corpus_root(None) == tmp_path


def test_corpus_root_that_is_not_a_directory_is_rejected(tmp_path: Path) -> None:
    target = tmp_path / "not-a-directory"
    target.write_text("", encoding="utf-8")

    with pytest.raises(harness.RegressionConfigError) as error:
        harness.resolve_corpus_root(str(target))

    assert "not a directory" in str(error.value)


def test_manifest_defaults_to_corpus_root(tmp_path: Path) -> None:
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text("", encoding="utf-8")

    assert harness.resolve_manifest(None, tmp_path) == manifest


def test_missing_manifest_is_a_config_error(tmp_path: Path) -> None:
    with pytest.raises(harness.RegressionConfigError) as error:
        harness.resolve_manifest(None, tmp_path)

    assert "manifest not found" in str(error.value)


def test_cli_without_corpus_root_exits_with_config_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(harness.CORPUS_ROOT_ENV, raising=False)

    exit_code = harness.main([])

    assert exit_code == harness.EXIT_CONFIG_ERROR
    assert "corpus root is required" in capsys.readouterr().err


def test_cli_rejects_unknown_case_id(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.delenv(harness.CORPUS_ROOT_ENV, raising=False)

    path = _write_cases(tmp_path, VALID_CASE)

    exit_code = harness.main(["--cases", str(path), "--case-id", "NOPE"])

    assert exit_code == harness.EXIT_CONFIG_ERROR
    assert "unknown case id" in capsys.readouterr().err


def test_cli_reports_malformed_fixture_before_touching_the_corpus(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = tmp_path / "cases.jsonl"
    path.write_text("{not json}\n", encoding="utf-8")

    exit_code = harness.main(["--cases", str(path), "--corpus-root", str(tmp_path)])

    assert exit_code == harness.EXIT_CONFIG_ERROR
    assert "invalid JSON" in capsys.readouterr().err
