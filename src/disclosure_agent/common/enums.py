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
    INVESTMENT_AMOUNT = "investment_amount"
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


class SectionScope(StrEnum):
    """Document-internal scope a question asks to be summarized.

    docs/agent_design/query_plan.md의 ``section_scope`` 어휘를 따른다. 다만
    ``whole_document``는 담지 않는다. Pattern B는 지정 범위를 찾지 못했을 때 문서 전체
    요약으로 대체하지 않으므로(docs/agent_design/retrieval_strategy.md Pattern B의
    Fallback), 그 값을 만들 자리가 없다. 값이 있으면 fallback이 생겼을 때 조용히 쓰인다.
    """

    FINANCIAL_STATEMENT = "financial_statement"
    BUSINESS_OVERVIEW = "business_overview"
    PRODUCTS = "products"
    FACILITY_INVESTMENT = "facility_investment"


class SummaryItemType(StrEnum):
    """Structural kind of one document-summary content unit."""

    PARAGRAPH = "paragraph"
    TABLE = "table"


class EvidenceLocationType(StrEnum):
    """Supported source locations for evidence."""

    TABLE = "table"
    EVENT_FIELD = "event_field"
    # 본문 단락. 표도 번호 필드도 아닌 서술 근거의 자리다. Pattern B의 요약 항목 중
    # 단락에서 온 것이 여기에 해당한다.
    TEXT = "text"


class EventType(StrEnum):
    """Supported event disclosure types."""

    SUPPLY_CONTRACT = "supply_contract"
    FACILITY_INVESTMENT = "facility_investment"


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


class ComparisonRelation(StrEnum):
    """Successful relation between two comparable inputs."""

    LEFT_GREATER = "left_greater"
    RIGHT_GREATER = "right_greater"
    EQUAL = "equal"


class ResultInputType(StrEnum):
    """Semantic result types accepted as inputs to derived results."""

    FACT = "fact"
    CALCULATION = "calculation"


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
    AMBIGUOUS_MATCH = "ambiguous_match"
    NOT_FOUND = "not_found"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"
    WITHHELD_IN_SOURCE = "withheld_in_source"

    PARTIAL_SOURCE_SUPPORT = "partial_source_support"

    NOT_COMPARABLE = "not_comparable"
    OUT_OF_DATA_RANGE = "out_of_data_range"
    OUT_OF_SCOPE_DATA = "out_of_scope_data"
    OUT_OF_SCOPE_POLICY = "out_of_scope_policy"


class ExecutionStatus(StrEnum):
    """Whether the Agent execution itself completed normally."""

    COMPLETED = "COMPLETED"
    BLOCKED = "BLOCKED"
    FAILED = "FAILED"
