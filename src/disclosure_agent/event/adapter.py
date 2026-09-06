"""Adapter from Event Extractor output to common Contract objects."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from disclosure_agent.common.enums import (
    EventType,
    EvidenceLocationType,
    FactAccountingScope,
    MetricConcept,
)
from disclosure_agent.common.schemas import (
    Event,
    Evidence,
    EvidenceProvenance,
    Fact,
    FieldChange,
    RawReference,
)

# Contract 지표와 Extractor canonical field의 대응.
#
# 대응표를 두는 이유는 두 어휘가 서로의 것이 아니기 때문이다. Extractor는 원문 표기에서 온
# 필드 이름을 쓰고, Contract는 질문이 요청하는 지표 개념을 쓴다. 이름이 우연히 같다고 해서
# 한쪽을 다른 쪽으로 대신 읽으면, 한쪽이 이름을 바꿀 때 조용히 끊어진다.
#
# 여기 없는 지표는 이 adapter가 만들지 않는다. 비슷한 필드로 대신하지 않고 거절한다.
CONTRACT_FIELD_BY_METRIC: dict[MetricConcept, str] = {
    MetricConcept.CONTRACT_AMOUNT: "contract_amount",
    MetricConcept.CONTRACT_START_DATE: "contract_start_date",
    MetricConcept.CONTRACT_END_DATE: "contract_end_date",
}


@dataclass(slots=True)
class EventAdapterResult:
    """Common Contract objects produced from one extracted event."""

    event: Event
    evidence: list[Evidence]
    field_changes: list[FieldChange]
    raw_reference: RawReference | None = None


@dataclass(slots=True)
class ContractFactAdapterResult:
    """Contract Facts and their Evidence produced from one extracted event.

    EventAdapterResult와 나누는 이유:
        저쪽은 이벤트 하나와 그 변경 이력을 표현하고(Q13), 이쪽은 질문이 요청한 값들을
        Fact로 표현한다(Q02). 한 타입에 담으면 이미 검증된 Q13의 Contract가 Q02 때문에
        흔들린다.
    """

    facts: list[Fact]
    evidence: list[Evidence]


class MissingContractFieldError(ValueError):
    """요청한 지표에 해당하는 필드가 문서에 없거나 값이 비어 있는 경우."""

    def __init__(self, metric: MetricConcept, field_name: str) -> None:
        super().__init__(
            f"문서에서 {metric.value}에 해당하는 필드({field_name})의 값을 찾지 "
            "못했습니다."
        )

        self.metric = metric
        self.field_name = field_name


class UnsupportedContractMetricError(ValueError):
    """이 adapter가 대응표를 갖고 있지 않은 지표를 요청받은 경우."""

    def __init__(self, metric: MetricConcept) -> None:
        supported = ", ".join(sorted(item.value for item in CONTRACT_FIELD_BY_METRIC))

        super().__init__(
            f"거래소공시 Fact로 옮길 수 없는 지표입니다: {metric.value}. "
            f"현재 대응하는 지표: {supported}."
        )

        self.metric = metric


def _field_evidence_id(
    doc_id: str,
    field_name: str,
) -> str:
    return f"evidence_{doc_id}_{field_name}"


def _change_evidence_id(
    doc_id: str,
    field_name: str,
    side: str,
) -> str:
    return f"evidence_{doc_id}_{field_name}_{side}"


def _make_provenance(
    provenance: dict[str, Any] | None,
    *,
    column_key: str,
) -> EvidenceProvenance | None:
    """Convert Extractor provenance into common EvidenceProvenance."""

    if not provenance:
        return None

    table_id = provenance.get("table_id")
    row_index = provenance.get("row_index")
    column_index = provenance.get(column_key)

    if (
        table_id is None
        and row_index is None
        and column_index is None
    ):
        return None

    return EvidenceProvenance(
        table_id=table_id,
        row_index=row_index,
        column_index=column_index,
    )


def _make_field_evidence(
    *,
    doc_id: str,
    field_name: str,
    field: dict[str, Any],
) -> Evidence:
    """Create Evidence for one normal extracted event field."""

    raw_group = field.get("raw_group")
    raw_value = field.get("raw_value")

    return Evidence(
        evidence_id=_field_evidence_id(
            doc_id,
            field_name,
        ),
        doc_id=doc_id,
        location_type=EvidenceLocationType.EVENT_FIELD,
        section_path=(
            [str(raw_group)]
            if raw_group
            else []
        ),
        row_label=field.get("raw_field"),
        field_name=field_name,
        raw_text=(
            str(raw_value)
            if raw_value is not None
            else None
        ),
        provenance=_make_provenance(
            field.get("provenance"),
            column_key="value_cell_index",
        ),
    )


def _make_event_attributes(
    fields: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build common Event attributes from canonical Extractor fields."""

    attributes: dict[str, Any] = {}

    for field_name, field in fields.items():
        attributes[field_name] = field.get(
            "normalized_value"
        )

        normalized_unit = field.get(
            "normalized_unit"
        )

        if normalized_unit is not None:
            attributes[
                f"{field_name}_unit"
            ] = normalized_unit

    return attributes


def _adapt_correction(
    *,
    doc_id: str,
    correction: dict[str, Any],
) -> tuple[
    list[FieldChange],
    list[Evidence],
    RawReference,
]:
    """Convert correction before/after rows into common Contracts."""

    changes: list[FieldChange] = []
    evidence: list[Evidence] = []

    for change in correction.get("changes", []):
        raw_field = change.get("raw_field")
        field_name = (
            change.get("canonical_field")
            or raw_field
            or "unknown"
        )

        provenance = change.get("provenance")

        before_evidence_id = _change_evidence_id(
            doc_id,
            str(field_name),
            "before",
        )
        after_evidence_id = _change_evidence_id(
            doc_id,
            str(field_name),
            "after",
        )

        before = change.get("before")
        after = change.get("after")

        before_evidence = Evidence(
            evidence_id=before_evidence_id,
            doc_id=doc_id,
            location_type=EvidenceLocationType.EVENT_FIELD,
            row_label=raw_field,
            field_name=str(field_name),
            raw_text=(
                str(before)
                if before is not None
                else None
            ),
            provenance=_make_provenance(
                provenance,
                column_key="before_cell_index",
            ),
        )

        after_evidence = Evidence(
            evidence_id=after_evidence_id,
            doc_id=doc_id,
            location_type=EvidenceLocationType.EVENT_FIELD,
            row_label=raw_field,
            field_name=str(field_name),
            raw_text=(
                str(after)
                if after is not None
                else None
            ),
            provenance=_make_provenance(
                provenance,
                column_key="after_cell_index",
            ),
        )

        evidence.extend(
            [
                before_evidence,
                after_evidence,
            ]
        )

        changes.append(
            FieldChange(
                field_name=str(field_name),
                raw_field=raw_field,
                before=before,
                after=after,
                unit=change.get("raw_unit"),
                before_evidence_id=before_evidence_id,
                after_evidence_id=after_evidence_id,
            )
        )

    raw_reference = RawReference(
        document_title=correction.get(
            "original_report_name"
        ),
        original_filing_date=correction.get(
            "original_filing_date"
        ),
        correction_filing_date=correction.get(
            "correction_date"
        ),
        correction_reason=correction.get("reason"),
    )

    return changes, evidence, raw_reference


def adapt_extracted_event(
    extracted: dict[str, Any],
) -> EventAdapterResult:
    """Adapt one Event Extractor result to the common Contract."""

    doc_id = str(extracted.get("doc_id") or "")
    company = str(extracted.get("company") or "")
    raw_event_type = str(
        extracted.get("event_type") or ""
    )

    if not doc_id:
        raise ValueError(
            "Event Adapter requires doc_id."
        )

    if not company:
        raise ValueError(
            "Event Adapter requires company."
        )

    try:
        event_type = EventType(raw_event_type)
    except ValueError as exc:
        raise ValueError(
            "Event type is not supported by the "
            f"common Contract: {raw_event_type!r}"
        ) from exc

    fields = extracted.get("fields") or {}

    evidence = [
        _make_field_evidence(
            doc_id=doc_id,
            field_name=field_name,
            field=field,
        )
        for field_name, field in fields.items()
    ]

    event = Event(
        event_id=str(
            extracted.get("event_id")
            or f"event_{doc_id}"
        ),
        event_type=event_type,
        company=company,
        source_doc_id=doc_id,
        attributes=_make_event_attributes(fields),
        is_correction=bool(
            extracted.get("is_correction")
        ),
    )

    field_changes: list[FieldChange] = []
    raw_reference: RawReference | None = None

    correction = extracted.get("correction")

    if correction:
        (
            field_changes,
            correction_evidence,
            raw_reference,
        ) = _adapt_correction(
            doc_id=doc_id,
            correction=correction,
        )

        evidence.extend(correction_evidence)

    return EventAdapterResult(
        event=event,
        evidence=evidence,
        field_changes=field_changes,
        raw_reference=raw_reference,
    )

def _normalized_text(value: Any) -> str | None:
    """Contract Fact가 담을 수 있는 표현으로 정규화 값을 옮긴다.

    문자열로 옮기는 이유:
        Fact.normalized_value는 ``float | str | None``이다. Extractor가 계약금액을 정수
        1933600000000으로 만드는데 그대로 넘기면 Contract가 float으로 바꾼다. 자릿수가 큰
        금액에서 그 변환은 원문과 대조할 수 없는 표현을 만든다.

        값을 다시 계산하지 않는다. Extractor가 만든 canonical 값을 표기만 옮긴다. 날짜는
        이미 ISO 문자열이므로 이 함수를 지나도 그대로다.

        periodic.adapter가 normalized_value에 ``str(...)``을 담는 것과 같은 규약이다.
    """
    if value is None:
        return None

    return str(value)


def adapt_contract_facts(
    extracted: dict[str, Any],
    *,
    company: str,
    metrics: Sequence[MetricConcept],
) -> ContractFactAdapterResult:
    """Adapt requested event fields of one extracted event to Contract Facts.

    요청한 지표를 전부 만들거나 하나도 만들지 않는다. 하나라도 없으면 예외를 올린다:
        Q02는 금액·시작일·종료일 세 값을 함께 묻는다. 두 개만 담아 돌려주면 받는 쪽이 그것으로
        답을 조립할 수 있고, 그러면 세 값을 물은 질문에 두 값만 답하는 결과가 된다.
        agent.period_retrieval의 PeriodFactCollection이 같은 이유로 부분 결과를 담지 않는다.

    company를 인자로 받는 이유:
        거래소공시 parsed document의 metadata.corp_name은 비어 있다. 문서 본문이 회사명을
        표로 담지 않기 때문이며, 그 값은 manifest에서 온다. 이 계층은 manifest를 모르므로
        (event -> retrieval 의존 금지) 확정된 값을 받는다.

    fiscal_year를 채우지 않는 이유:
        계약금액과 계약기간은 회계기간의 값이 아니다. 질문의 2025년은 공시를 찾는 기준점이지
        이 값들이 속한 회계연도가 아니며, 실제로 계약 종료일은 2029년일 수 있다. 기준점을
        회계연도 자리에 적으면 받는 쪽이 "2025년 계약금액"으로 읽는다.

    accounting_scope가 not_applicable인 이유:
        거래소공시에는 연결·별도 구분이 없다. unknown은 "확인하지 못했다"는 뜻이라 사실과
        다르다.

    단위 정책은 여기서 정하지 않는다. Extractor가 만든 normalized_unit(계약금액의 "KRW")을
    그대로 싣는다. 정기공시의 "원"과 표기가 다른 것은 알려진 미결정 사항이며, 이 자리에서
    한쪽으로 맞추면 그 결정이 코드에 숨은 채로 굳는다.
    """
    doc_id = str(extracted.get("doc_id") or "")

    if not doc_id:
        raise ValueError("Contract Fact Adapter requires doc_id.")

    if not company:
        raise ValueError("Contract Fact Adapter requires company.")

    fields = extracted.get("fields") or {}

    facts: list[Fact] = []
    evidence: list[Evidence] = []

    for metric in metrics:
        field_name = CONTRACT_FIELD_BY_METRIC.get(metric)

        if field_name is None:
            raise UnsupportedContractMetricError(metric)

        field = fields.get(field_name)

        if not field or field.get("normalized_value") is None:
            raise MissingContractFieldError(metric, field_name)

        evidence_id = _field_evidence_id(doc_id, field_name)

        evidence.append(
            _make_field_evidence(
                doc_id=doc_id,
                field_name=field_name,
                field=field,
            )
        )

        facts.append(
            Fact(
                fact_id=f"fact_{doc_id}_{metric.value}",
                company=company,
                metric=metric,
                fiscal_year=None,
                fiscal_period=None,
                accounting_scope=FactAccountingScope.NOT_APPLICABLE,
                raw_value=field.get("raw_value"),
                raw_unit=field.get("raw_unit"),
                normalized_value=_normalized_text(field.get("normalized_value")),
                normalized_unit=field.get("normalized_unit"),
                evidence_ids=[evidence_id],
            )
        )

    return ContractFactAdapterResult(facts=facts, evidence=evidence)
