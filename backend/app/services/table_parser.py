"""
Simple table parser and classifier stub.

This minimal implementation exists to unblock server startup for Milestone A.
We'll expand it in Milestone B to actually map rows into SQL models.
"""
from typing import List, Dict, Any, Optional


class TableParser:
    """Classify and parse tables extracted from PDFs.

    For now, provides naive header-based classification and a passthrough
    structure for rows. Real parsing will be implemented next.
    """

    CAPITAL_CALL_KEYS = {"capital call", "contribution", "call"}
    DISTRIBUTION_KEYS = {"distribution", "return", "dividend", "income"}
    ADJUSTMENT_KEYS = {"adjustment", "recall", "rebalance"}

    def classify(self, headers: List[str]) -> Optional[str]:
        """Classify table type by header keywords."""
        h = " ".join([x.lower() for x in headers])
        if any(k in h for k in self.CAPITAL_CALL_KEYS):
            return "capital_calls"
        if any(k in h for k in self.DISTRIBUTION_KEYS):
            return "distributions"
        if any(k in h for k in self.ADJUSTMENT_KEYS):
            return "adjustments"
        return None

    def parse(self, headers: List[str], rows: List[List[Any]]) -> Dict[str, Any]:
        """Return a simple normalized dict; mapping is postponed to Milestone B."""
        return {
            "headers": headers,
            "rows": rows,
            "columns": [h.strip().lower().replace(" ", "_") for h in headers],
        }
