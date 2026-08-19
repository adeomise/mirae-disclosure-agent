"""Shared enumerations for the Disclosure Agent contract v0.1."""

from enum import StrEnum


class QueryAccountingScope(StrEnum):
    """Accounting scope requested by the user."""

    CONSOLIDATED = "consolidated"
    SEPARATE = "separate"
    UNSPECIFIED = "unspecified"
    NOT_APPLICABLE = "not_applicable"


class FactAccountingScope(StrEnum):
    """Accounting scope established for an extracted fact."""

    CONSOLIDATED = "consolidated"
    SEPARATE = "separate"
    UNKNOWN = "unknown"
    NOT_APPLICABLE = "not_applicable"


class MetricConcept(StrEnum):
    """Metrics supported by the first vertical slices."""

    REVENUE = "revenue"
    OPERATING_INCOME = "operating_income"
    CONTRACT_AMOUNT = "contract_amount"
    CONTRACT_START_DATE = "contract_start_date"
    CONTRACT_END_DATE = "contract_end_date"
    HOLDING_SHARES = "holding_shares"
    HOLDING_RATIO = "holding_ratio"


class Intent(StrEnum):
    """Supported query intents."""

    SINGLE_FACT = "single_fact"
    EVENT_HISTORY = "event_history"


class DocumentClass(StrEnum):
    """High-level disclosure document classes."""

    PERIODIC = "periodic"
    EVENT = "event"
    HOLDING = "holding"


class ChainPolicy(StrEnum):
    """Policy for selecting documents in an event chain."""

    ORIGINAL_ONLY = "original_only"
    LATEST_VALID = "latest_valid"
    FULL_CHAIN = "full_chain"


class EvidenceLocationType(StrEnum):
    """Supported source locations for evidence."""

    TABLE = "table"
    EVENT_FIELD = "event_field"


class EventType(StrEnum):
    """Supported event disclosure types."""

    SUPPLY_CONTRACT = "supply_contract"


class EventLinkRelation(StrEnum):
    """Relationship from a disclosure to another disclosure."""

    CORRECTION_OF = "correction_of"
    FOLLOWUP_OF = "followup_of"
    TERMINATION_OF = "termination_of"


class EventLinkResolutionStatus(StrEnum):
    """Outcome of resolving a raw disclosure reference."""

    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    NOT_FOUND = "not_found"
    OUT_OF_CORPUS = "out_of_corpus"


class AnswerabilityStatus(StrEnum):
    """Whether the available evidence supports an answer."""

    ANSWERABLE = "ANSWERABLE"
    PARTIALLY_ANSWERABLE = "PARTIALLY_ANSWERABLE"
    UNANSWERABLE = "UNANSWERABLE"
    UNRESOLVED = "UNRESOLVED"


class AnswerabilityReason(StrEnum):
    """Machine-readable reason for a non-fully-answerable response."""

    INCOMPLETE_CHAIN = "incomplete_chain"
    AMBIGUOUS_LINK = "ambiguous_link"
    NOT_FOUND = "not_found"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
