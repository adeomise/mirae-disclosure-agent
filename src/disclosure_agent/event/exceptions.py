"""Domain failures raised while resolving event disclosure state."""

from __future__ import annotations


class UnsupportedCompositeCorrectionError(Exception):
    """A correction cannot be represented safely by the current field model."""

    def __init__(self, *, source_doc_id: str, category: str) -> None:
        self.source_doc_id = source_doc_id
        self.category = category
        super().__init__(
            "latest state blocked by unsupported correction: "
            f"source_doc_id={source_doc_id!r}, category={category!r}"
        )
