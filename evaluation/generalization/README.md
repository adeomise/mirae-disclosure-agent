# Generalization hold-out dataset

This directory contains a separate, corpus-verified hold-out set. It deliberately
mirrors `evaluation/questions.jsonl` and `evaluation/gold_answers.jsonl`, but is not
loaded by the default evaluation runner and does not change any representative
question lifecycle.

The gold records are structure-first. They pin stable semantic fields, independently
verified normalized Fact values and units, collection cardinality, calculation
operations, and reference integrity without using answer prose as truth. Each expected
Fact also pins its source-cell Evidence ID. This is the narrow provenance assertion
available in the shared evaluation schema: it identifies the intended periodic filing
and source cell without asserting unrelated Evidence records.

The snapshot was verified against the corpus configured by
`DISCLOSURE_CORPUS_ROOT` and `DISCLOSURE_CORPUS_MANIFEST` on 2026-09-04. Revalidate
both JSONL files with `evaluation.runner.load_dataset`, execute the cases with
`evaluation.runner.execute_agent_cases`, and compare them with
`evaluation.runner.evaluate_predictions`. Always pass these paths explicitly; do not
copy the cases into the representative dataset.

The live 30/30 result produced while constructing this dataset is construction
validation, not an unbiased generalization score. The cases were selected and verified
against this corpus snapshot and the current merged production behavior; production
code was not tuned against them.

No `WITHHELD_IN_SOURCE`, `INCOMPLETE_CHAIN`, or `AMBIGUOUS_MATCH` cases are included.
The candidate search did not establish a defensible current-production case for
those reasons, so none was forced into the gold set.
