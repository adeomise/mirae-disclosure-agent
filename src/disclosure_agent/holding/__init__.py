"""Safe extraction boundary for major-shareholding disclosures."""

from disclosure_agent.holding.extractor import (
    HoldingExtractionResult,
    extract_holding_facts,
)
from disclosure_agent.holding.parser import parse_holding_xml

__all__ = ["HoldingExtractionResult", "extract_holding_facts", "parse_holding_xml"]
