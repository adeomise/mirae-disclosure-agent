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

The comparator supports optional exact collection counts, multiple partial Fact
expectations, canonical execution/answerability fields, and Fact/Calculation to
Evidence reference integrity. Expected Fact fields that are omitted are not
compared. Event, EventLink, EventHistory, and Calculation content rubrics remain
future extensions; their collection counts can already be checked.

Local prediction and evaluation result files belong under `results/` and must not
contain source documents or secrets.
