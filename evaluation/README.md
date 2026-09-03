# Evaluation

`questions.jsonl` and `gold_answers.jsonl` currently contain explicitly synthetic
contract cases. They do not represent real disclosure values or successful Agent
execution. The same schemas also support deterministic regression cases without
putting real source documents or guessed disclosure values in the repository.

Run deterministic validation with:

```text
uv run python -m evaluation.runner
```

Without predictions, the runner preserves the original dataset-validation mode. It
validates paired case IDs, answerability expectations, fact fields, legacy
event-history expectations, and minimum evidence requirements.

## Deterministic prediction evaluation

Predictions use the common `AgentExecutionResult` fields plus `case_id`, one JSON
object per line. The runner does not execute the Agent or access a corpus.

```text
uv run python -m evaluation.runner --predictions path/to/predictions.jsonl
```

Only questions with `evaluation_mode="regression"` and `lifecycle="active"` are
scored. `future` cases preserve final target gold without recording today's
`BLOCKED` or unimplemented behavior as the expected result; they are reported
separately and excluded from the score denominator. Existing contract cases omit
`lifecycle` and retain the backward-compatible `active` default, but contract cases
are validation-only and are not regression-scored.

Active and contract `ANSWERABLE` cases still require a supported semantic gold
expectation (`fact`, `facts`, or `event_history`) and Evidence. A future regression
case may instead use structural gold: at least one specified collection count or a
positive `min_evidence_count`. Completely empty future gold is rejected.

The comparator supports optional exact collection counts, multiple partial Fact
expectations, canonical execution/answerability fields, and non-empty Fact/Calculation
to Evidence reference integrity. Expected Fact fields that are omitted are not
compared. Calculation gold can fix operation/unit order and independently recompute
`delta`/`delta_pct` values from their ordered Fact inputs. Event, EventLink, and
EventHistory content rubrics remain future extensions; their collection counts can
already be checked.

## Corpus-dependent gold values

Some representative gold values depend on what the current corpus contains, not only on
the question text. `q07` ("한미반도체 매출이 작년보다 얼마나 늘었어?") is the clearest case.

Its relative period is resolved against a fiscal-year anchor: the latest fiscal year for
which the company has an annual report in the corpus. The system clock is never used, and
the anchor never moves because a metric could not be extracted
(`docs/agent_design/open_decisions.md` D8). In the current corpus snapshot 한미반도체 has
annual reports for FY2023, FY2024 and FY2025, so the anchor is **FY2025** and the gold
Facts are pinned to **FY2024 and FY2025**.

If the corpus later gains an FY2026 annual report, the same question means FY2025 vs
FY2026. Keep the question text as it is and move the expected fiscal years; Q07 validates
relative-period resolution itself, not a particular pair of years.

`as_of_date` and `corpus_snapshot` on `QuestionCase` stay `null`. No explicit as-of
contract exists yet, and `q07` deliberately exercises the default anchor path.

Local prediction and evaluation result files belong under `results/` and must not
contain source documents or secrets.

## Opt-in live Agent evaluation

Live mode executes the active regression cases through the public Agent entry point
and evaluates the returned `AgentExecutionResult` objects with the same deterministic
comparator:

The current active regression set contains exactly five representative questions:
`Q01`, `Q02`, `Q07`, `Q13`, and `Q18`. Therefore, a reported live-Agent result of `5/5`
applies only to those five active cases; it is not a result for all 18 representative
questions.

```text
uv run python -m evaluation.runner --run-agent --corpus-root path/to/corpus
```

If the manifest is not at the corpus root's default `manifest.jsonl` location, pass
it explicitly:

```text
uv run python -m evaluation.runner --run-agent --corpus-root path/to/corpus --manifest path/to/manifest.jsonl
```

Live execution never activates from `DATA_ROOT` or another environment variable.
`--run-agent` and an existing `--corpus-root` directory are both required. Contract
cases remain validation-only, and future regression cases remain outside the score
denominator. A returned blocked or failed execution retains its Agent status and
reason for comparison; an unexpected exception identifies the failing case and
terminates the command with a nonzero exit code.

### Temporal metadata

`QuestionCase.as_of_date` and `QuestionCase.corpus_snapshot` are currently
evaluation metadata only.

Live Agent execution does not pass these fields into `answer_question`, because
the current public Agent execution contract does not expose corresponding
runtime inputs.

If temporal/as-of execution becomes part of the Agent contract, the live
evaluation runner should forward these fields explicitly so reproducibility is
preserved.
