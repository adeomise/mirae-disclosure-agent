"""Q01 periodic retrieval, extraction, and response assembly."""

from pathlib import Path

from disclosure_agent.common.enums import AnswerabilityStatus
from disclosure_agent.common.schemas import AnswerResponse, QueryPlan
from disclosure_agent.periodic.extractor import extract_q01_revenue
from disclosure_agent.retrieval.service import retrieve_periodic_documents


class PeriodicQ01Service:
    """Answer Q01 from local periodic XML documents."""

    def __init__(self, corpus_dir: str | Path) -> None:
        self.corpus_dir = Path(corpus_dir)

    def answer(self, query_plan: QueryPlan) -> AnswerResponse:
        """Return a fact only when one unambiguous annual candidate exists."""
        parsed_documents = retrieve_periodic_documents(query_plan, self.corpus_dir)
        # Temporary ambiguity guard until correction/latest-version metadata exists.
        if len(parsed_documents) != 1:
            reason = (
                "No matching annual periodic document was resolved."
                if not parsed_documents
                else "Multiple annual periodic document versions are ambiguous."
            )
            return AnswerResponse(answer=reason, status=AnswerabilityStatus.UNRESOLVED)

        result = extract_q01_revenue(parsed_documents[0], query_plan)
        if result is None:
            return AnswerResponse(
                answer="The requested-year consolidated revenue fact was not resolved.",
                status=AnswerabilityStatus.UNRESOLVED,
            )

        fact, evidence = result
        return AnswerResponse(
            answer=(
                f"{fact.company} {fact.fiscal_year} consolidated revenue: "
                f"{fact.raw_value} {fact.raw_unit}"
            ),
            status=AnswerabilityStatus.ANSWERABLE,
            facts=[fact],
            evidence=[evidence],
        )
