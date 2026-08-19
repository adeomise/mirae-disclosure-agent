"""Deterministic identifiers for resolved event chains."""

from disclosure_agent.common.enums import EventType


def make_event_id(event_type: EventType | str, root_doc_id: str) -> str:
    """Build a stable ID from event type and an established canonical root doc."""
    event_type_value = (
        event_type.value if isinstance(event_type, EventType) else str(event_type)
    )
    canonical_root = root_doc_id.strip()
    if not event_type_value or not canonical_root:
        raise ValueError("event_type and canonical root_doc_id are required")
    return f"event_{event_type_value}_{canonical_root}"
