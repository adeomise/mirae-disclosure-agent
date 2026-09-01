"""실측 코퍼스에 대한 Contract 회귀 측정 도구.

무엇을 재는가:
    질문 하나를 `disclosure_agent.agent.router.answer_question`에 그대로 넣고, 돌아온
    `AgentExecutionResult`가 사전에 적어 둔 Contract 수준 기대와 맞는지 본다.

무엇을 재지 않는가:
    자연어 답변의 정답 여부는 재지 않는다. 지금 파이프라인은 자연어를 만들지 않으며,
    실제 수치를 저장소에 적지 않는다는 `AGENTS.md` 규칙과도 충돌한다. 그래서 기대값은
    상태·건수·연결 관계처럼 **실제 공시 값을 적지 않고도 확인할 수 있는 것**만 담는다.

왜 production 코드 밖에 두는가:
    `evaluation/runner.py`는 합성 케이스의 Gold 구조를 검증하는 도구이고
    (`real_agent_execution: BLOCKED`), 이 스크립트는 실측 코퍼스로 Agent를 실제로
    실행한다. 목적이 다르고, 코퍼스는 로컬에만 있다(`data/raw/`는 Git 제외). 두 가지를
    한 모듈에 섞으면 코퍼스가 없는 환경에서 평가 도구 전체가 못 돌게 된다.

사용:
    python scripts/run_corpus_regression.py --corpus-root <path>
    python scripts/run_corpus_regression.py --corpus-root <path> --case-id Q01 --json

종료 코드:
    0  모든 케이스 PASS
    1  하나 이상 FAIL
    2  설정 오류(코퍼스 경로 없음, fixture 형식 오류 등)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from disclosure_agent.common.schemas import AgentExecutionResult

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CASES_PATH = Path(__file__).resolve().parent / "corpus_regression_cases.jsonl"

CORPUS_ROOT_ENV = "DISCLOSURE_CORPUS_ROOT"
MANIFEST_ENV = "DISCLOSURE_CORPUS_MANIFEST"

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_CONFIG_ERROR = 2

# fixture가 쓸 수 있는 기대 항목.
#
# 목록에 없는 key는 조용히 무시하지 않고 설정 오류로 본다. 오타 하나가 "검사하지 않음"으로
# 바뀌면 PASS가 아무것도 보증하지 못한다.
SUPPORTED_EXPECTATION_KEYS: frozenset[str] = frozenset(
    {
        "execution_status",
        "answerability_status",
        "answerability_reason",
        "issue_stage",
        "issue_reason",
        "facts",
        "min_facts",
        "fact_accounting_scope",
        "evidence",
        "min_evidence",
        "event_links",
        "min_event_links",
        "event_histories",
        "chain_complete",
        "document_summaries",
        "min_document_summary_items",
        "calculations",
        "comparisons",
        "calculation_units",
        "evidence_linkage",
        "result_input_linkage",
        "conversion_gaps_pending",
    }
)


class RegressionConfigError(Exception):
    """fixture나 실행 인자가 잘못되어 측정을 시작할 수 없는 경우."""


@dataclass(frozen=True)
class RegressionCase:
    """질문 하나와 그 질문에 대한 Contract 수준 기대."""

    case_id: str
    question: str
    expect: dict[str, Any]
    pattern: str | None = None
    notes: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------
# fixture 읽기
# --------------------------------------------------------------------------


def _parse_case(record: Any, *, origin: str) -> RegressionCase:
    if not isinstance(record, dict):
        raise RegressionConfigError(f"{origin}: case must be a JSON object")

    case_id = record.get("id") or record.get("case_id")

    if not isinstance(case_id, str) or not case_id.strip():
        raise RegressionConfigError(f"{origin}: case needs a non-empty 'id'")

    question = record.get("question")

    if not isinstance(question, str) or not question.strip():
        raise RegressionConfigError(f"{origin}: case '{case_id}' needs a 'question'")

    expect = record.get("expect")

    if not isinstance(expect, dict) or not expect:
        raise RegressionConfigError(
            f"{origin}: case '{case_id}' needs a non-empty 'expect' object"
        )

    unknown = sorted(set(expect) - SUPPORTED_EXPECTATION_KEYS)

    if unknown:
        raise RegressionConfigError(
            f"{origin}: case '{case_id}' has unsupported expectation keys: "
            f"{', '.join(unknown)}"
        )

    tags = record.get("tags", [])

    if not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags):
        raise RegressionConfigError(f"{origin}: case '{case_id}' has a malformed 'tags'")

    return RegressionCase(
        case_id=case_id,
        question=question,
        expect=dict(expect),
        pattern=record.get("pattern"),
        notes=record.get("notes", ""),
        tags=tuple(tags),
    )


def load_cases(path: str | Path) -> list[RegressionCase]:
    """
    JSONL fixture를 읽는다.

    한 줄에 케이스 하나다. 빈 줄은 건너뛴다. 형식이 어긋나면 몇 번째 줄인지와 함께 올린다.
    어디가 잘못됐는지 모르는 오류는 fixture를 고칠 수 없게 만든다.
    """
    path = Path(path)

    if not path.exists():
        raise RegressionConfigError(f"case fixture not found: {path}")

    cases: list[RegressionCase] = []
    seen: set[str] = set()

    for line_number, line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), start=1
    ):
        if not line.strip():
            continue

        origin = f"{path}:{line_number}"

        try:
            record = json.loads(line)

        except json.JSONDecodeError as error:
            raise RegressionConfigError(f"{origin}: invalid JSON ({error.msg})") from error

        case = _parse_case(record, origin=origin)

        if case.case_id in seen:
            raise RegressionConfigError(f"{origin}: duplicate case id '{case.case_id}'")

        seen.add(case.case_id)
        cases.append(case)

    if not cases:
        raise RegressionConfigError(f"{path}: fixture has no cases")

    return cases


# --------------------------------------------------------------------------
# 기대 대조
# --------------------------------------------------------------------------


def _enum_value(value: Any) -> Any:
    """StrEnum이면 문자열 값으로, 아니면 그대로."""
    return getattr(value, "value", value)


def _check_equal(failures: list[str], label: str, expected: Any, actual: Any) -> None:
    actual = _enum_value(actual)

    if actual != expected:
        failures.append(f"{label}: expected {expected!r}, got {actual!r}")


def _check_at_least(failures: list[str], label: str, minimum: Any, actual: int) -> None:
    if not isinstance(minimum, int):
        raise RegressionConfigError(f"{label} must be an integer, got {minimum!r}")

    if actual < minimum:
        failures.append(f"{label}: expected >= {minimum}, got {actual}")


def _check_exact_count(failures: list[str], label: str, expected: Any, actual: int) -> None:
    if not isinstance(expected, int):
        raise RegressionConfigError(f"{label} must be an integer, got {expected!r}")

    if actual != expected:
        failures.append(f"{label}: expected {expected}, got {actual}")


def _check_fact_accounting_scope(
    failures: list[str],
    expected: Any,
    result: AgentExecutionResult,
) -> None:
    """
    돌아온 Fact 전부가 기대한 회계 기준인지 본다.

    건수만 세는 검사로는 연결·별도 혼동을 잡지 못한다. '별도 기준' 질문에 연결 Fact 1건이
    돌아와도 facts=1은 그대로 만족하기 때문이다. Q01 계열의 주된 실패 모드가 이것이므로
    scope는 건수와 별개로 확인한다.

    첫 Fact만 보지 않는다. 여러 건이 섞여 돌아오는 회귀에서 앞 하나만 맞으면 통과해
    나머지가 다른 기준이라는 사실이 가려진다.
    """
    if not result.facts:
        failures.append(
            f"fact_accounting_scope: expected a fact with scope {expected!r}, got none"
        )

        return

    for fact in result.facts:
        _check_equal(
            failures,
            f"fact_accounting_scope[{fact.fact_id}]",
            expected,
            fact.accounting_scope,
        )


def _check_evidence_linkage(failures: list[str], result: AgentExecutionResult) -> None:
    """
    Fact와 CalculationResult, FieldChange가 가리키는 Evidence가 결과 안에 있는지 본다.

    근거 없는 Fact는 계산에도 답변에도 쓸 수 없다는 것이 설계 원칙이므로, 건수만 세는
    검사로는 회귀를 잡지 못한다. 연결이 끊긴 채 Evidence 개수만 맞는 결과가 나올 수 있다.

    계산 결과도 같은 규칙을 따른다. 원문에 없는 값일수록 어디서 나왔는지가 남아 있어야 한다.
    """
    known = {evidence.evidence_id for evidence in result.evidence}

    for fact in result.facts:
        if not fact.evidence_ids:
            failures.append(f"evidence_linkage: fact '{fact.fact_id}' has no evidence_ids")
            continue

        dangling = sorted(set(fact.evidence_ids) - known)

        if dangling:
            failures.append(
                f"evidence_linkage: fact '{fact.fact_id}' references unknown evidence "
                f"{dangling}"
            )

    for calculation in result.calculations:
        if not calculation.evidence_ids:
            failures.append(
                f"evidence_linkage: calculation '{calculation.calculation_id}' "
                "has no evidence_ids"
            )
            continue

        dangling = sorted(set(calculation.evidence_ids) - known)

        if dangling:
            failures.append(
                f"evidence_linkage: calculation '{calculation.calculation_id}' "
                f"references unknown evidence {dangling}"
            )

    for summary in result.document_summaries:
        for item in summary.items:
            if not item.evidence_ids:
                failures.append(
                    f"evidence_linkage: summary item '{item.item_id}' "
                    "has no evidence_ids"
                )
                continue

            dangling = sorted(set(item.evidence_ids) - known)

            if dangling:
                failures.append(
                    f"evidence_linkage: summary item '{item.item_id}' "
                    f"references unknown evidence {dangling}"
                )

    for history in result.event_histories:
        for change in history.changes:
            for side in ("before_evidence_id", "after_evidence_id"):
                evidence_id = getattr(change, side)

                if evidence_id is not None and evidence_id not in known:
                    failures.append(
                        f"evidence_linkage: change '{change.field_name}'.{side} references "
                        f"unknown evidence '{evidence_id}'"
                    )


def _check_result_input_linkage(failures: list[str], result: AgentExecutionResult) -> None:
    """
    파생 결과가 가리키는 입력이 같은 결과 안에 있는지 본다.

    값과 근거가 맞아도 "무엇에서 나온 값인가"가 결과 밖을 가리키면 되짚을 수 없다.
    evaluation/runner.py의 result_input_references 검사와 같은 성질을 실측 경로에서도 본다.
    """
    known: dict[str, set[str]] = {
        "fact": {fact.fact_id for fact in result.facts},
        "calculation": {item.calculation_id for item in result.calculations},
    }

    for calculation in result.calculations:
        if not calculation.inputs:
            failures.append(
                f"result_input_linkage: calculation '{calculation.calculation_id}' "
                "has no inputs"
            )
            continue

        dangling = sorted(
            f"{reference.input_type.value}:{reference.input_id}"
            for reference in calculation.inputs
            if reference.input_id not in known[reference.input_type.value]
        )

        if dangling:
            failures.append(
                f"result_input_linkage: calculation '{calculation.calculation_id}' "
                f"references unknown inputs {dangling}"
            )

    for comparison in result.comparisons:
        dangling = sorted(
            f"{reference.input_type.value}:{reference.input_id}"
            for reference in (comparison.left, comparison.right)
            if reference.input_id not in known[reference.input_type.value]
        )

        if dangling:
            failures.append(
                f"result_input_linkage: comparison '{comparison.comparison_id}' "
                f"references unknown inputs {dangling}"
            )


def _check_calculation_units(
    failures: list[str],
    expected: Any,
    result: AgentExecutionResult,
) -> None:
    """
    계산 결과의 단위를 순서대로 대조한다.

    값은 담지 않는다. 실제 공시 수치를 저장소에 적지 않는다는 규칙 때문이며, 단위는 값이
    아니라 분류이므로 적을 수 있다. 백분율과 percentage point가 뒤바뀌면 받는 쪽이 차이를
    비율로 읽으므로 개수만으로는 잡히지 않는다.
    """
    actual = [calculation.unit for calculation in result.calculations]

    if actual != list(expected):
        failures.append(f"calculation_units: expected {list(expected)}, got {actual}")


def check_expectations(
    expect: dict[str, Any],
    result: AgentExecutionResult,
) -> list[str]:
    """
    기대와 실행 결과를 대조하고 어긋난 항목을 전부 돌려준다.

    첫 실패에서 멈추지 않는다. 한 번 실행에 무엇이 몇 개 어긋났는지 봐야 원인을 좁힐 수 있다.
    """
    failures: list[str] = []

    if "execution_status" in expect:
        _check_equal(
            failures, "execution_status", expect["execution_status"], result.execution_status
        )

    if "answerability_status" in expect:
        _check_equal(
            failures,
            "answerability_status",
            expect["answerability_status"],
            result.answerability_status,
        )

    if "answerability_reason" in expect:
        _check_equal(
            failures,
            "answerability_reason",
            expect["answerability_reason"],
            result.answerability_reason,
        )

    issue = result.execution_issue

    if "issue_stage" in expect:
        _check_equal(failures, "issue_stage", expect["issue_stage"], issue.stage if issue else None)

    if "issue_reason" in expect:
        _check_equal(
            failures, "issue_reason", expect["issue_reason"], issue.reason if issue else None
        )

    if "facts" in expect:
        _check_exact_count(failures, "facts", expect["facts"], len(result.facts))

    if "min_facts" in expect:
        _check_at_least(failures, "min_facts", expect["min_facts"], len(result.facts))

    if "fact_accounting_scope" in expect:
        _check_fact_accounting_scope(failures, expect["fact_accounting_scope"], result)

    if "evidence" in expect:
        _check_exact_count(failures, "evidence", expect["evidence"], len(result.evidence))

    if "min_evidence" in expect:
        _check_at_least(failures, "min_evidence", expect["min_evidence"], len(result.evidence))

    if "event_links" in expect:
        _check_exact_count(failures, "event_links", expect["event_links"], len(result.event_links))

    if "min_event_links" in expect:
        _check_at_least(
            failures, "min_event_links", expect["min_event_links"], len(result.event_links)
        )

    if "event_histories" in expect:
        _check_exact_count(
            failures, "event_histories", expect["event_histories"], len(result.event_histories)
        )

    if "chain_complete" in expect:
        if not result.event_histories:
            failures.append("chain_complete: expected an event history, got none")
        else:
            _check_equal(
                failures,
                "chain_complete",
                expect["chain_complete"],
                result.event_histories[0].chain_complete,
            )

    if "document_summaries" in expect:
        _check_exact_count(
            failures,
            "document_summaries",
            expect["document_summaries"],
            len(result.document_summaries),
        )

    if "min_document_summary_items" in expect:
        # 항목 수는 문서 구조에 종속되므로 최소만 둔다. 실제 공시 값을 적지 않는다는
        # 규칙과 별개로, 개수를 못박으면 같은 질문이 코퍼스가 바뀔 때마다 FAIL이 된다.
        _check_at_least(
            failures,
            "min_document_summary_items",
            expect["min_document_summary_items"],
            sum(len(summary.items) for summary in result.document_summaries),
        )

    if "calculations" in expect:
        _check_exact_count(
            failures, "calculations", expect["calculations"], len(result.calculations)
        )

    if "comparisons" in expect:
        _check_exact_count(
            failures, "comparisons", expect["comparisons"], len(result.comparisons)
        )

    if "calculation_units" in expect:
        _check_calculation_units(failures, expect["calculation_units"], result)

    if "conversion_gaps_pending" in expect:
        pending = sum(1 for gap in result.conversion_gaps if gap.pending_decision)
        _check_exact_count(
            failures,
            "conversion_gaps_pending",
            expect["conversion_gaps_pending"],
            pending,
        )

    if expect.get("evidence_linkage"):
        _check_evidence_linkage(failures, result)

    if expect.get("result_input_linkage"):
        _check_result_input_linkage(failures, result)

    return failures


# --------------------------------------------------------------------------
# 실행
# --------------------------------------------------------------------------


def summarize_result(result: AgentExecutionResult) -> dict[str, Any]:
    """실행 결과에서 회귀 비교에 쓰는 값만 뽑는다. 실제 공시 값은 담지 않는다."""
    issue = result.execution_issue

    return {
        "execution_status": _enum_value(result.execution_status),
        "answerability_status": _enum_value(result.answerability_status),
        "answerability_reason": _enum_value(result.answerability_reason),
        "issue_stage": issue.stage if issue else None,
        "issue_reason": issue.reason if issue else None,
        "facts": len(result.facts),
        # 어떤 회계 기준이 선택됐는지만 남긴다. 값이 아니라 분류이므로 실제 공시 수치를
        # 적지 않는다는 규칙과 어긋나지 않고, FAIL을 볼 때 무엇이 잘못 골렸는지 바로 보인다.
        "fact_accounting_scope": sorted(
            {_enum_value(fact.accounting_scope) for fact in result.facts}
        ),
        "evidence": len(result.evidence),
        "event_links": len(result.event_links),
        "event_histories": len(result.event_histories),
        "chain_complete": (
            result.event_histories[0].chain_complete if result.event_histories else None
        ),
        "document_summaries": len(result.document_summaries),
        # 항목 수는 건수이지 값이 아니다. 범위가 번지거나 좁아진 것이 개수로 먼저 드러난다.
        "document_summary_items": sum(
            len(summary.items) for summary in result.document_summaries
        ),
        # 어느 범위를 요약했는지만 남긴다. 분류이므로 실제 공시 내용을 적지 않는다.
        "document_summary_scopes": sorted(
            {_enum_value(summary.section_scope) for summary in result.document_summaries}
        ),
        "calculations": len(result.calculations),
        "comparisons": len(result.comparisons),
        # 연산과 단위는 분류이므로 남긴다. 값은 남기지 않는다.
        "calculation_operations": [item.operation for item in result.calculations],
        "calculation_units": [item.unit for item in result.calculations],
        "warnings": len(result.warnings),
        "conversion_gaps": len(result.conversion_gaps),
        "conversion_gaps_pending": sum(
            1 for gap in result.conversion_gaps if gap.pending_decision
        ),
    }


def run_case(
    case: RegressionCase,
    *,
    corpus_root: Path,
    manifest_path: Path | None,
) -> dict[str, Any]:
    """
    케이스 하나를 실행하고 판정한다.

    Agent가 올린 예상치 못한 예외는 여기서 ERROR로 기록한다. 한 케이스가 터졌다고 나머지
    측정을 못 하게 되면 회귀 도구로서 쓸모가 없다. 다만 예외를 PASS/FAIL과 섞지 않고
    별도 판정으로 남긴다.
    """
    # router는 여기서 import한다. 코퍼스가 없는 환경에서도 fixture 검증과 matcher 단위
    # 테스트가 돌아야 하고, 그 경로는 Agent 구현 전체를 필요로 하지 않는다.
    from disclosure_agent.agent.router import answer_question

    record: dict[str, Any] = {
        "id": case.case_id,
        "pattern": case.pattern,
        "question": case.question,
        "tags": list(case.tags),
    }

    try:
        result = answer_question(
            case.question,
            corpus_root=corpus_root,
            manifest_path=manifest_path,
        )

    except Exception as error:  # noqa: BLE001 - 측정 도구는 케이스 하나에 멈추지 않는다
        record["verdict"] = "ERROR"
        record["failures"] = [f"{type(error).__name__}: {error}"]
        record["actual"] = None

        return record

    failures = check_expectations(case.expect, result)

    record["verdict"] = "PASS" if not failures else "FAIL"
    record["failures"] = failures
    record["actual"] = summarize_result(result)

    return record


def run_cases(
    cases: list[RegressionCase],
    *,
    corpus_root: Path,
    manifest_path: Path | None,
) -> dict[str, Any]:
    """전체 케이스를 실행하고 집계 결과를 만든다."""
    records = [
        run_case(case, corpus_root=corpus_root, manifest_path=manifest_path) for case in cases
    ]

    counts = {"PASS": 0, "FAIL": 0, "ERROR": 0}

    for record in records:
        counts[record["verdict"]] += 1

    return {
        "corpus_root": str(corpus_root),
        "manifest_path": str(manifest_path) if manifest_path else None,
        "case_count": len(records),
        "passed": counts["PASS"],
        "failed": counts["FAIL"],
        "errored": counts["ERROR"],
        "cases": records,
    }


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def resolve_corpus_root(argument: str | None) -> Path:
    """CLI 인자 > 환경변수 순으로 코퍼스 경로를 정한다. 없으면 설정 오류다."""
    raw = argument or os.environ.get(CORPUS_ROOT_ENV)

    if not raw:
        raise RegressionConfigError(
            "corpus root is required: pass --corpus-root or set "
            f"{CORPUS_ROOT_ENV}. 실측 코퍼스는 Git에 포함되지 않으므로 로컬 경로를 직접 "
            "지정해야 합니다."
        )

    corpus_root = Path(raw).expanduser()

    if not corpus_root.is_dir():
        raise RegressionConfigError(f"corpus root is not a directory: {corpus_root}")

    return corpus_root


def resolve_manifest(argument: str | None, corpus_root: Path) -> Path:
    """manifest 경로를 정한다. 기본값은 corpus_root/manifest.jsonl이다."""
    raw = argument or os.environ.get(MANIFEST_ENV)

    manifest_path = Path(raw).expanduser() if raw else corpus_root / "manifest.jsonl"

    if not manifest_path.is_file():
        raise RegressionConfigError(f"manifest not found: {manifest_path}")

    return manifest_path


def _render_text(summary: dict[str, Any]) -> str:
    lines = [
        f"corpus : {summary['corpus_root']}",
        f"cases  : {summary['case_count']}",
        "",
    ]

    for record in summary["cases"]:
        lines.append(f"[{record['verdict']:<5}] {record['id']}  {record['question']}")

        actual = record["actual"]

        if actual is not None:
            units = ",".join(unit or "-" for unit in actual["calculation_units"])

            items = (
                f"({actual['document_summary_items']} items)"
                if actual["document_summaries"]
                else ""
            )

            lines.append(
                "         "
                f"exec={actual['execution_status']} "
                f"ans={actual['answerability_status']} "
                f"reason={actual['answerability_reason']} "
                f"facts={actual['facts']} "
                f"scope={','.join(actual['fact_accounting_scope']) or '-'} "
                f"evidence={actual['evidence']} "
                f"links={actual['event_links']} histories={actual['event_histories']} "
                f"summaries={actual['document_summaries']}{items} "
                f"calc={actual['calculations']}{f'({units})' if units else ''} "
                f"comparisons={actual['comparisons']}"
            )

        for failure in record["failures"]:
            lines.append(f"         - {failure}")

    lines.append("")
    lines.append(
        f"PASS {summary['passed']} / FAIL {summary['failed']} / ERROR {summary['errored']}"
    )

    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_corpus_regression",
        description="실측 코퍼스로 AgentExecutionResult Contract 회귀를 측정합니다.",
    )
    parser.add_argument(
        "--corpus-root",
        default=None,
        help=f"공시 코퍼스 루트. 미지정 시 환경변수 {CORPUS_ROOT_ENV}를 사용합니다.",
    )
    parser.add_argument(
        "--manifest",
        default=None,
        help="manifest.jsonl 경로. 기본값은 <corpus-root>/manifest.jsonl 입니다.",
    )
    parser.add_argument(
        "--cases",
        default=str(DEFAULT_CASES_PATH),
        help="회귀 케이스 JSONL 경로.",
    )
    parser.add_argument(
        "--case-id",
        action="append",
        default=None,
        dest="case_ids",
        help="실행할 케이스 id. 여러 번 지정할 수 있습니다.",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="사람이 읽는 표 대신 JSON 요약을 출력합니다.",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="JSON 요약을 파일로도 저장합니다.",
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        cases = load_cases(args.cases)

        if args.case_ids:
            wanted = set(args.case_ids)
            missing = sorted(wanted - {case.case_id for case in cases})

            if missing:
                raise RegressionConfigError(f"unknown case id(s): {', '.join(missing)}")

            cases = [case for case in cases if case.case_id in wanted]

        corpus_root = resolve_corpus_root(args.corpus_root)
        manifest_path = resolve_manifest(args.manifest, corpus_root)

    except RegressionConfigError as error:
        print(f"error: {error}", file=sys.stderr)

        return EXIT_CONFIG_ERROR

    summary = run_cases(cases, corpus_root=corpus_root, manifest_path=manifest_path)

    rendered = json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True)

    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")

    print(rendered if args.json else _render_text(summary))

    return EXIT_OK if summary["failed"] == 0 and summary["errored"] == 0 else EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main())
