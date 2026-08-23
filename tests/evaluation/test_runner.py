"""Tests for the public deterministic evaluation runner command."""

import json
import subprocess
import sys
from pathlib import Path

REPOSITORY_ROOT = Path(__file__).parents[2]


def run_runner(*arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "evaluation.runner", *arguments],
        cwd=REPOSITORY_ROOT,
        capture_output=True,
        check=False,
        text=True,
    )


def test_default_dataset_reports_deterministic_metrics() -> None:
    completed = run_runner()

    assert completed.returncode == 0
    assert json.loads(completed.stdout) == {
        "mode": "case_validation",
        "real_agent_execution": "BLOCKED",
        "case_count": 3,
        "synthetic_case_count": 3,
        "paired_case_count": 3,
        "answerable_cases_requiring_evidence": 2,
        "unanswerable_cases_without_fact": 1,
        "event_history_case_count": 1,
    }


def test_runner_rejects_answerable_case_without_evidence(tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    gold = tmp_path / "gold.jsonl"
    questions.write_text(
        json.dumps(
            {
                "case_id": "invalid",
                "question": "[SYNTHETIC] question",
                "synthetic": True,
                "evaluation_mode": "contract",
            }
        ),
        encoding="utf-8",
    )
    gold.write_text(
        json.dumps(
            {
                "case_id": "invalid",
                "status": "ANSWERABLE",
                "fact": {
                    "company": "Synthetic Company",
                    "metric": "revenue",
                    "accounting_scope": "consolidated",
                },
                "min_evidence_count": 0,
            }
        ),
        encoding="utf-8",
    )

    completed = run_runner("--questions", str(questions), "--gold", str(gold))

    assert completed.returncode != 0
    assert "answerable cases must require evidence" in completed.stderr


def test_runner_rejects_unpaired_case_ids(tmp_path: Path) -> None:
    questions = tmp_path / "questions.jsonl"
    gold = tmp_path / "gold.jsonl"
    questions.write_text(
        json.dumps(
            {
                "case_id": "question-only",
                "question": "[SYNTHETIC] question",
                "synthetic": True,
                "evaluation_mode": "contract",
            }
        ),
        encoding="utf-8",
    )
    gold.write_text(
        json.dumps(
            {
                "case_id": "gold-only",
                "status": "UNANSWERABLE",
                "reason": "not_found",
                "min_evidence_count": 0,
            }
        ),
        encoding="utf-8",
    )

    completed = run_runner("--questions", str(questions), "--gold", str(gold))

    assert completed.returncode != 0
    assert "unpaired evaluation cases" in completed.stderr
