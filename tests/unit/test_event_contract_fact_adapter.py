"""거래소공시 필드 -> Contract Fact 변환 테스트.

검증 대상:
- 요청한 지표를 Fact로 만들고 각 Fact가 자기 Evidence를 가리키는가
- 회계 축이 없는 값(계약금액·계약기간)에 회계연도·연결기준을 지어내지 않는가
- 하나라도 만들 수 없으면 부분 결과를 돌려주지 않는가

값은 합성이다. 실제 공시 수치를 저장소에 적지 않는다(AGENTS.md).

Q13 경로(adapt_extracted_event)는 이 slice에서 바뀌지 않아야 하므로 마지막에 회귀만
확인한다. 그쪽 Contract의 상세는 tests/unit/test_event_adapter.py가 고정하고 있다.
"""

from __future__ import annotations

from typing import Any

import pytest

from disclosure_agent.common.enums import (
    EventType,
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.event.adapter import (
    MissingContractFieldError,
    UnsupportedContractMetricError,
    adapt_contract_facts,
    adapt_extracted_event,
)

DOC_ID = "exchange_SYNTHETIC0001"

COMPANY = "테스트조선(주)"

REQUESTED = (
    MetricConcept.CONTRACT_AMOUNT,
    MetricConcept.CONTRACT_START_DATE,
    MetricConcept.CONTRACT_END_DATE,
)


def _field(
    *,
    raw_group: str,
    raw_field: str,
    raw_value: str,
    normalized_value: Any,
    raw_unit: str | None = None,
    normalized_unit: str | None = None,
    row_index: int = 0,
) -> dict[str, Any]:
    return {
        "raw_group": raw_group,
        "raw_field": raw_field,
        "raw_value": raw_value,
        "raw_unit": raw_unit,
        "normalized_value": normalized_value,
        "normalized_unit": normalized_unit,
        "provenance": {
            "table_id": "XFormD1_Form0_Table0",
            "row_index": row_index,
            "field_cell_index": 0,
            "value_cell_index": 1,
        },
    }


def _extracted(**overrides: Any) -> dict[str, Any]:
    """Event Extractor가 공급계약 1건에서 내놓는 형태의 합성 결과."""
    fields: dict[str, dict[str, Any]] = {
        "counterparty": _field(
            raw_group="계약상대",
            raw_field="계약상대",
            raw_value="테스트해운",
            normalized_value="테스트해운",
            row_index=6,
        ),
        "contract_amount": _field(
            raw_group="계약내역",
            raw_field="계약금액(원)",
            raw_value="1,111,100,000,000",
            raw_unit="원",
            normalized_value=1111100000000,
            normalized_unit="KRW",
            row_index=2,
        ),
        "contract_start_date": _field(
            raw_group="계약기간",
            raw_field="시작일",
            raw_value="2025-09-16",
            normalized_value="2025-09-16",
            row_index=9,
        ),
        "contract_end_date": _field(
            raw_group="계약기간",
            raw_field="종료일",
            raw_value="2029-05-31",
            normalized_value="2029-05-31",
            row_index=10,
        ),
    }

    extracted: dict[str, Any] = {
        "event_id": f"event_{DOC_ID}",
        "event_type": "supply_contract",
        "doc_id": DOC_ID,
        "company": None,
        "fields": fields,
    }

    extracted.update(overrides)

    return extracted


# --------------------------------------------------------------------------
# 1. 요청한 지표가 Fact가 된다
# --------------------------------------------------------------------------


def test_contract_amount_becomes_fact_linked_to_its_evidence() -> None:
    """
    근거 없는 Fact는 답변에 쓸 수 없다.

    참조가 결과 안에서 실제로 풀리는지 본다. 개수만 세면 연결이 끊긴 채로도 통과한다.
    회계 축을 함께 보는 이유: 거래소공시에는 연결·별도 구분이 없고 계약금액은 회계연도의
    값이 아니다. 질문의 연도를 fiscal_year에 적으면 받는 쪽이 "2025년 계약금액"으로 읽는다.
    """
    adapted = adapt_contract_facts(
        _extracted(),
        company=COMPANY,
        metrics=(MetricConcept.CONTRACT_AMOUNT,),
    )

    assert [fact.metric for fact in adapted.facts] == [MetricConcept.CONTRACT_AMOUNT]

    # 요청하지 않은 counterparty는 같은 문서의 필드지만 담기지 않는다.
    assert len(adapted.evidence) == 1

    fact = adapted.facts[0]

    assert fact.company == COMPANY
    assert fact.accounting_scope is FactAccountingScope.NOT_APPLICABLE
    assert fact.fiscal_year is None
    assert fact.fiscal_period is None

    # 자릿수가 큰 금액을 float으로 옮기면 원문과 대조할 수 없는 표현이 된다.
    assert fact.raw_value == "1,111,100,000,000"
    assert fact.raw_unit == "원"
    assert fact.normalized_value == "1111100000000"
    assert fact.normalized_unit == "KRW"

    evidence = adapted.evidence[0]

    assert fact.evidence_ids == [evidence.evidence_id]
    assert evidence.evidence_id == f"evidence_{DOC_ID}_contract_amount"
    assert evidence.doc_id == DOC_ID
    assert evidence.location_type is EvidenceLocationType.EVENT_FIELD
    assert evidence.field_name == "contract_amount"
    assert evidence.section_path == ["계약내역"]
    assert evidence.row_label == "계약금액(원)"
    assert evidence.raw_text == "1,111,100,000,000"

    assert evidence.provenance is not None
    assert evidence.provenance.table_id == "XFormD1_Form0_Table0"
    assert evidence.provenance.row_index == 2
    assert evidence.provenance.column_index == 1


@pytest.mark.parametrize(
    ("metric", "field_name", "expected_value"),
    [
        (MetricConcept.CONTRACT_START_DATE, "contract_start_date", "2025-09-16"),
        (MetricConcept.CONTRACT_END_DATE, "contract_end_date", "2029-05-31"),
    ],
)
def test_contract_period_dates_become_facts(
    metric: MetricConcept,
    field_name: str,
    expected_value: str,
) -> None:
    """날짜는 이미 ISO 문자열이므로 변환을 지나도 그대로다. 단위는 붙지 않는다."""
    adapted = adapt_contract_facts(
        _extracted(),
        company=COMPANY,
        metrics=(metric,),
    )

    fact = adapted.facts[0]

    assert fact.metric is metric
    assert fact.normalized_value == expected_value
    assert fact.normalized_unit is None
    assert fact.evidence_ids == [f"evidence_{DOC_ID}_{field_name}"]

    assert {item.evidence_id for item in adapted.evidence} == set(fact.evidence_ids)


def test_requested_metrics_become_facts_in_order() -> None:
    """개수만 맞추지 않는다. 어느 지표가 어느 순서로 나왔는지까지 본다."""
    adapted = adapt_contract_facts(_extracted(), company=COMPANY, metrics=REQUESTED)

    assert [fact.metric for fact in adapted.facts] == list(REQUESTED)

    known = {item.evidence_id for item in adapted.evidence}

    for fact in adapted.facts:
        assert fact.evidence_ids
        assert set(fact.evidence_ids) <= known


# --------------------------------------------------------------------------
# 2. 만들 수 없으면 부분 결과를 돌려주지 않는다
# --------------------------------------------------------------------------


def test_missing_requested_field_stops_the_whole_conversion() -> None:
    """
    두 값만 담아 돌려주면 받는 쪽이 그것으로 답을 조립할 수 있다.

    세 값을 물은 질문에 두 값만 답하는 결과가 되고, 사라진 값이 어디에도 남지 않는다.
    """
    extracted = _extracted()

    del extracted["fields"]["contract_end_date"]

    with pytest.raises(MissingContractFieldError) as error:
        adapt_contract_facts(extracted, company=COMPANY, metrics=REQUESTED)

    assert error.value.field_name == "contract_end_date"


def test_empty_normalized_value_is_treated_as_missing() -> None:
    """
    필드는 있는데 값이 비어 있는 경우가 실제로 있다(공시유보).

    필드가 있다는 것만으로 Fact를 만들면 값이 None인 Fact가 근거까지 갖춘 채 나간다.
    """
    extracted = _extracted()

    extracted["fields"]["contract_amount"]["normalized_value"] = None

    with pytest.raises(MissingContractFieldError):
        adapt_contract_facts(extracted, company=COMPANY, metrics=REQUESTED)


def test_unsupported_metric_is_refused() -> None:
    """대응표에 없는 지표를 비슷한 필드로 대신 읽지 않는다."""
    with pytest.raises(UnsupportedContractMetricError):
        adapt_contract_facts(
            _extracted(),
            company=COMPANY,
            metrics=(MetricConcept.REVENUE,),
        )


# --------------------------------------------------------------------------
# 3. 기존 Q13 경로는 이 slice에서 바뀌지 않는다
# --------------------------------------------------------------------------


def test_adapt_extracted_event_is_unchanged() -> None:
    """
    같은 파일에 Q02 변환을 더했다고 Q13 경로가 달라지면 안 된다.

    Evidence는 요청 지표가 아니라 문서의 모든 필드에서 나오고, Event.attributes는 canonical
    값을 그대로 담는다. 두 경로가 같은 _make_field_evidence를 쓰므로 근거 표현도 한 벌이다.
    """
    adapted = adapt_extracted_event(_extracted(company=COMPANY))

    assert adapted.event.event_type is EventType.SUPPLY_CONTRACT
    assert adapted.event.company == COMPANY
    assert adapted.event.source_doc_id == DOC_ID
    assert adapted.field_changes == []
    assert adapted.raw_reference is None

    # 요청 지표만 담는 Q02와 달리 문서의 네 필드가 모두 근거로 남는다.
    assert len(adapted.evidence) == 4

    assert adapted.event.attributes["contract_amount"] == 1111100000000
    assert adapted.event.attributes["contract_amount_unit"] == "KRW"
    assert adapted.event.attributes["contract_end_date"] == "2029-05-31"
