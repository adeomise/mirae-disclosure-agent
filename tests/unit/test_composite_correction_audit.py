from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "audit_composite_correction_rows.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("audit_composite_correction_rows", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_script = _load_script()
aggregate_occurrences = audit_script.aggregate_occurrences
classify_occurrence = audit_script.classify_occurrence
detect_pattern = audit_script.detect_pattern


def _occurrence(
    receipt_no: str,
    corp_name: str,
    *,
    pattern: str = "contract_amount_and_revenue_ratio",
    classification: str = "source_composite",
) -> dict[str, object]:
    return {
        "doc_id": f"exchange_{receipt_no}",
        "receipt_no": receipt_no,
        "corp_name": corp_name,
        "event_type": "supply_contract",
        "report_nm": "[기재정정]합성공시",
        "normalized_pattern": pattern,
        "row_index": 7,
        "raw_field": "2. 계약내역 - 계약금액(원) - 매출액대비(%)",
        "raw_group": "계약내역 - 계약금액(원) - 매출액대비",
        "raw_unit": "%",
        "before": "100 10.0",
        "after": "200 20.0",
        "table_id": "XFormD8_Form0_Table0",
        "cell_indexes": {"field": 0, "before": 1, "after": 2},
        "classification": classification,
    }


def test_target_composite_detection_and_non_composite_exclusion() -> None:
    assert (
        detect_pattern("2.계약내역 - 계약금액(원) - 매출액대비(%)")
        == "contract_amount_and_revenue_ratio"
    )
    assert detect_pattern("5. 계약기간 - 시작일 - 종료일") == "contract_period_start_and_end"
    assert detect_pattern("2. 계약내역 - 계약금액(원)") is None


def test_classification_uses_structure_and_preserves_container_ambiguity() -> None:
    assert classify_occurrence("contract_details_container", {"td_count": 3}) == (
        "ambiguous_container"
    )
    assert (
        classify_occurrence(
            "contract_period_start_and_end", {"td_count": 4, "has_nested_table": False}
        )
        == "parser_splittable"
    )
    assert (
        classify_occurrence(
            "contract_period_start_and_end", {"td_count": 3, "has_nested_table": False}
        )
        == "source_composite"
    )


def test_aggregation_deduplicates_companies_receipts_and_bounds_samples() -> None:
    items = [
        _occurrence("R2", "회사A"),
        _occurrence("R1", "회사A"),
        _occurrence("R1", "회사A"),
        _occurrence("R3", "회사B"),
    ]
    result = aggregate_occurrences(items, sample_limit=2)
    details = result["patterns"]["contract_amount_and_revenue_ratio"]
    assert details["occurrences"] == 4
    assert details["unique_receipts"] == 3
    assert details["unique_company_count"] == 2
    assert [sample["receipt_no"] for sample in details["representative_samples"]] == [
        "R1",
        "R3",
    ]


def test_aggregation_is_deterministic_marks_mixed_and_serializes_json() -> None:
    first = _occurrence("R2", "회사B", classification="parser_splittable")
    second = _occurrence("R1", "회사A")
    forward = aggregate_occurrences([first, second])
    reverse = aggregate_occurrences([second, first])
    assert forward == reverse
    details = forward["patterns"]["contract_amount_and_revenue_ratio"]
    assert details["classification_counts"] == {
        "parser_splittable": 1,
        "source_composite": 1,
    }
    assert details["mixed_template"] is True
    assert json.loads(json.dumps(forward, ensure_ascii=False)) == forward


def test_aggregation_preserves_classification_metadata() -> None:
    item = _occurrence("R1", "회사A")
    result = aggregate_occurrences([item], sample_limit=1)
    sample = result["patterns"]["contract_amount_and_revenue_ratio"]["representative_samples"][0]
    assert sample["raw_unit"] == "%"
    assert sample["cell_indexes"] == {"field": 0, "before": 1, "after": 2}
    assert sample["classification"] == "source_composite"
