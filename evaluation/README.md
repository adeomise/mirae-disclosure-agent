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
expectation (`fact`, `facts`, `document_summaries`, or `event_history`) and Evidence.
A future regression case may instead use structural gold: at least one specified
collection count or a positive `min_evidence_count`. Completely empty future gold is
rejected.

The comparator supports optional exact collection counts, multiple partial Fact and
DocumentSummary expectations, canonical execution/answerability fields, and non-empty
Fact/Calculation/DocumentSummary to Evidence reference integrity. DocumentSummary
integrity can also require one selected filing and require all Evidence to belong to it.
Expected fields that are omitted are not compared. Calculation gold can fix
operation/unit/value/input/Evidence structure and
independently recompute `delta`/`delta_pct` or `aggregate` values from their Fact inputs.
Comparison gold can fix its typed left/right inputs and relation. The generic
EventHistory evaluator supports exact `document_count` and `change_count`,
`link_resolution`, and `chain_complete`. Its provenance integrity checks Evidence
coverage for `history.document_ids`, requires each FieldChange's referenced Evidence
to belong to that history's chain, and requires each EventLink's source and resolved
documents to belong to the same history chain. Unrelated Evidence that is not referenced
by an EventHistory or FieldChange is not restricted. Value-level EventHistory and other
content rubrics remain future extensions.

## Semantic count contract

Some questions ask "how many times" ("몇 번") about something a result represents with
more than one structured collection. `counts` alone pins raw collection sizes but does
not say which of those sizes is the actual answer to the user's question — it is a
structural check, not a claim about meaning. `semantic_count` is that claim:

```json
"semantic_count": {"source": "event_links", "value": 3}
```

`source` names the structured collection on the predicted result whose length is the
semantic count answer; `value` is the expected length. The comparator computes
`len(getattr(prediction, source))` and compares it to `value`, independently of any
`counts` entry for the same collection. `source` is restricted to structured collections
this contract currently commits to as a valid count answer — today only `event_links` —
not every collection on the result; it is not resolved per `case_id`.

### Q15: what "몇 번" counts

Q15 ("레인보우로보틱스 사옥·제조시설 신축 투자, 그동안 계획이 몇 번 바뀌었어?") is the
representative case for this contract. Its EventHistory chain has:

- `document_count = 4`: the root filing plus 3 correction filings (chain length).
- `event_links = 3`: one correction link per correction filing — this is the semantic
  answer count. Q15's gold sets `semantic_count.source = "event_links"` and
  `semantic_count.value = 3`.
- `len(history.changes) = 4`: individual FieldChange entries (e.g. one correction
  changes both the investment amount and the completion date, producing two
  FieldChanges from one correction event). FieldChange cardinality is **not** the
  user-facing "몇 번" count — it is a finer-grained record of what changed within each
  correction, not of how many corrections occurred.

`counts.event_links` and `event_history.document_count` in Q15's gold continue to pin
the underlying collection sizes as before; `semantic_count` additionally marks
`event_links` as the collection whose cardinality answers the question, so this
distinction cannot silently drift if `counts` or `event_history` gold changes for
unrelated reasons.

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

The current active regression set contains exactly thirteen representative questions:
`Q01`, `Q02`, `Q03`, `Q04`, `Q05`, `Q07`, `Q08`, `Q10`, `Q12`, `Q13`, `Q15`, `Q17`, and `Q18`.
Therefore, a reported live-Agent result of `13/13` applies only to those thirteen active
cases; it is not a result for all 18 representative questions.

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
