# Evaluation

`questions.jsonl` and `gold_answers.jsonl` contain explicitly synthetic contract
cases. They do not represent real disclosure values or successful Agent execution.

Run deterministic validation with:

```text
uv run python -m evaluation.runner
```

The runner validates paired case IDs, answerability expectations, fact fields,
event-history expectations, and minimum evidence requirements. It reports real
Agent execution as blocked until the public Agent entry point returns the common
`AnswerResponse` contract. Local execution results belong under `results/`.
