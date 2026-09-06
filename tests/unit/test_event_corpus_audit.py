import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from disclosure_agent.event.adapter import adapt_extracted_event
from disclosure_agent.event.extractor import extract_event
from disclosure_agent.event.parser import parse_exchange_document

SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "run_event_corpus_audit.py"


def _load_script() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_event_corpus_audit", SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


audit_script = _load_script()
EXIT_CONFIG_ERROR = audit_script.EXIT_CONFIG_ERROR
audit_records = audit_script.audit_records
main = audit_script.main
render_summary = audit_script.render_summary


def _record(
    receipt_no: str,
    subtype: str,
    *,
    correction: bool = False,
    file_path: str | None = None,
) -> dict:
    return {
        "doc_id": f"exchange_{receipt_no}",
        "corp_name": "테스트기업",
        "report_nm": subtype,
        "rcept_no": receipt_no,
        "rcept_dt": "20250101",
        "doc_group": "exchange",
        "doc_subtype": subtype,
        "is_correction": correction,
        "file_path": file_path or f"raw/exchange/테스트기업/{receipt_no}",
    }


def _write_document(root: Path, receipt_no: str, body: str) -> None:
    directory = root / "raw" / "exchange" / "테스트기업" / receipt_no
    directory.mkdir(parents=True)
    (directory / f"{receipt_no}.xml").write_text(
        f"<html><body>{body}</body></html>", encoding="utf-8"
    )


def test_audit_filters_counts_isolates_failures_and_audits_changes(tmp_path: Path) -> None:
    records = [
        _record("001", "단일판매공급계약체결"),
        _record("002", "단일판매공급계약체결", correction=True),
        _record("003", "신규시설투자등"),
        _record("004", "신규시설투자등", correction=True),
        _record("005", "단일판매공급계약체결"),
        _record("006", "단일판매공급계약해지"),
        {**_record("007", "단일판매공급계약체결"), "doc_group": "periodic"},
    ]
    normal_supply = """
      <span>단일판매ㆍ공급계약 체결</span>
      <table><tr><td>2. 계약내역</td><td>계약금액(원)</td><td>1,000</td></tr></table>
    """
    correction = """
      <span>정정신고(보고)</span><span>단일판매ㆍ공급계약 체결</span>
      <table id="XFormD8_Form0_RepeatTable0">
        <tr><td>정정항목</td><td>정정전</td><td>정정후</td></tr>
        <tr><td>3. 계약상대</td><td>전</td><td>후</td></tr>
        <tr><td>알수없는항목</td><td>A</td><td>B</td></tr>
        <tr><td>알수없는항목</td><td>C</td><td>D</td></tr>
      </table>
    """
    documents = (
        ("001", normal_supply),
        ("002", correction),
        ("003", "x"),
        ("004", correction),
    )
    for receipt_no, body in documents:
        _write_document(tmp_path, receipt_no, body)

    def parser(path: Path, **kwargs):
        if path.stem == "003":
            raise UnicodeError("parser raw payload must not stop the corpus")
        return parse_exchange_document(path, **kwargs)

    def extractor(document: dict):
        if document["metadata"]["doc_id"] == "exchange_004":
            raise ValueError("extractor failed")
        return extract_event(document)

    def adapter(extracted: dict):
        if extracted["doc_id"] == "exchange_002":
            raise RuntimeError("adapter failed")
        return adapt_extracted_event(extracted)

    summary = audit_records(
        records,
        corpus_root=tmp_path,
        failure_sample_limit=1,
        parser=parser,
        extractor=extractor,
        adapter=adapter,
    )

    assert summary["total_manifest_records"] == 7
    assert summary["supported_documents"] == 5
    assert summary["by_event_type"] == {"supply_contract": 3, "facility_investment": 2}
    assert summary["by_correction_status"] == {"original": 3, "correction": 2}
    assert summary["by_event_type_and_correction_status"] == {
        "supply_contract": {"original": 2, "correction": 1},
        "facility_investment": {"original": 1, "correction": 1},
    }
    assert summary["source_resolve_success"] == 4
    assert summary["source_resolve_failure"] == 1
    assert summary["parser_success"] == 3
    assert summary["parser_failure"] == 1
    assert summary["extractor_success"] == 2
    assert summary["extractor_failure"] == 1
    assert summary["adapter_success"] == 1
    assert summary["adapter_failure"] == 1
    assert summary["correction_documents"] == 2
    assert summary["mapped_field_changes"] == 1
    assert summary["unmapped_field_changes"] == 2
    assert summary["unmapped_raw_field_counts"] == {"알수없는항목": 2}
    assert summary["failure_by_stage"] == {
        "source_resolution": 1,
        "parser": 1,
        "extractor": 1,
        "adapter": 1,
    }
    assert all(len(samples) == 1 for samples in summary["failure_samples"].values())


def test_summary_and_json_output_are_deterministic(tmp_path: Path, capsys) -> None:
    _write_document(
        tmp_path,
        "001",
        "<span>단일판매ㆍ공급계약 체결</span><table><tr><td>계약상대</td><td>A</td></tr></table>",
    )
    manifest = tmp_path / "manifest.jsonl"
    manifest.write_text(
        json.dumps(_record("001", "단일판매공급계약체결"), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    output = tmp_path / "result.json"

    assert main(["--corpus-root", str(tmp_path), "--json-out", str(output)]) == 0
    stdout = capsys.readouterr().out
    saved = json.loads(output.read_text(encoding="utf-8"))

    assert stdout == render_summary(saved) + "\n"
    assert saved["supported_documents"] == 1
    assert stdout.index("supply_contract:") < stdout.index("facility_investment:")


def test_invalid_configuration_returns_nonzero(tmp_path: Path, capsys) -> None:
    assert main(["--corpus-root", str(tmp_path)]) == EXIT_CONFIG_ERROR
    assert "manifest not found" in capsys.readouterr().err
