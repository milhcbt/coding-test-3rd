"""
Table parser and classifier with basic row mapping.

Expands the initial stub to support Milestone B by:
- Classifying table type by header keywords
- Normalizing headers and mapping rows into transaction-shaped dicts
- Parsing dates and currency amounts robustly
"""
from typing import List, Dict, Any, Optional
from datetime import datetime
import re


class TableParser:
    """Classify and parse tables extracted from PDFs.

    Provides header-based classification and normalized entries for:
    - capital_calls
    - distributions
    - adjustments
    """

    CAPITAL_CALL_KEYS = {"capital call", "contribution", "call"}
    DISTRIBUTION_KEYS = {"distribution", "return", "dividend", "income"}
    ADJUSTMENT_KEYS = {"adjustment", "recall", "rebalance", "rebalance of", "rebalancing"}

    # Common header synonyms normalized to canonical keys
    HEADER_ALIASES = {
        # Dates
        "date": "date",
        "call_date": "date",
        "distribution_date": "date",
        "adjustment_date": "date",
        # Amounts
        "amount": "amount",
        "value": "amount",
        "total": "amount",
        "sum": "amount",
        # Types
        "type": "type",
        "category": "category",
        "call_number": "type",
        # Flags
        "recallable": "recallable",
        "is_recallable": "recallable",
        # Descriptions
        "description": "description",
        "memo": "description",
        "notes": "description",
    }

    def classify(self, headers: List[str]) -> Optional[str]:
        """Classify table type by header keywords (token-based to avoid substrings)."""
        h = " ".join([x.lower() for x in headers])
        # Tokenize on non-letters to avoid matching 'call' in 'recallable'
        tokens = set(re.findall(r"[a-z]+", h))
        normalized = [self._normalize_header(x) for x in headers]

        # Prefer distribution when 'recallable' column is present
        if "recallable" in normalized and "amount" in normalized:
            return "distributions"

        # Direct keyword tokens
        if any(k for k in ["distribution", "return", "dividend", "income"] if k in tokens):
            return "distributions"

        if any(k for k in ["adjustment", "rebalance", "rebalancing", "recall"] if k in tokens):
            return "adjustments"

        if ("call" in tokens or "contribution" in tokens or "capital" in tokens) and "amount" in normalized:
            return "capital_calls"

        # Heuristics
        if "category" in normalized and "amount" in normalized:
            return "adjustments"
        if "date" in normalized and "amount" in normalized and "type" in normalized:
            # Ambiguous generic table; default to capital calls least risky
            return "capital_calls"
        return None

    def parse(self, headers: List[str], rows: List[List[Any]]) -> Dict[str, Any]:
        """Parse headers+rows into normalized entries with inferred table type.

        Returns a dict: {"type": str, "items": List[Dict], "raw": {headers, rows}}
        """
        table_type = self.classify(headers)
        normalized_headers = [self._normalize_header(h) for h in headers]

        items: List[Dict[str, Any]] = []
        for row in rows:
            if not row or all((cell is None or str(cell).strip() == "") for cell in row):
                continue
            # Align row length to headers length
            row = list(row) + [None] * (len(normalized_headers) - len(row))
            row_dict = {normalized_headers[i]: (row[i] if i < len(row) else None) for i in range(len(normalized_headers))}

            # Normalize common fields
            date_val = self._parse_date(row_dict.get("date"))
            amount_val = self._parse_amount(row_dict.get("amount"))
            desc_val = self._safe_str(row_dict.get("description"))
            type_val = self._safe_str(row_dict.get("type"))

            if table_type == "capital_calls":
                item = {
                    "call_date": date_val,
                    "amount": amount_val,
                    "call_type": type_val or None,
                    "description": desc_val or None,
                }
                # Only keep plausible rows
                if item["call_date"] and item["amount"] is not None:
                    items.append(item)

            elif table_type == "distributions":
                recallable_raw = self._safe_str(row_dict.get("recallable")).lower()
                is_recallable = recallable_raw in {"yes", "y", "true", "1"}
                item = {
                    "distribution_date": date_val,
                    "amount": amount_val,
                    "distribution_type": type_val or None,
                    "is_recallable": is_recallable,
                    "description": desc_val or None,
                }
                if item["distribution_date"] and item["amount"] is not None:
                    items.append(item)

            elif table_type == "adjustments":
                category_val = self._safe_str(row_dict.get("category")) or None
                is_contrib_adj = False
                t = (type_val or "") + " " + (category_val or "")
                t_lower = t.lower()
                if any(k in t_lower for k in ["capital", "call", "contribution"]):
                    is_contrib_adj = True
                item = {
                    "adjustment_date": date_val,
                    "amount": amount_val,
                    "adjustment_type": type_val or category_val or None,
                    "category": category_val,
                    "is_contribution_adjustment": is_contrib_adj,
                    "description": desc_val or None,
                }
                if item["adjustment_date"] and item["amount"] is not None:
                    items.append(item)

            else:
                # Unknown type; return raw structure for debugging
                pass

        return {
            "type": table_type,
            "items": items,
            "raw": {
                "headers": headers,
                "rows": rows,
                "columns": normalized_headers,
            },
        }

    # ------------------------
    # Helpers
    # ------------------------
    def _normalize_header(self, h: Any) -> str:
        s = self._safe_str(h).strip().lower().replace(" ", "_")
        return self.HEADER_ALIASES.get(s, s)

    def _safe_str(self, v: Any) -> str:
        return "" if v is None else str(v).strip()

    def _parse_date(self, v: Any) -> Optional[datetime.date]:
        s = self._safe_str(v)
        if not s:
            return None
        # Try multiple common formats
        fmts = [
            "%Y-%m-%d",
            "%m/%d/%Y",
            "%d/%m/%Y",
            "%d-%b-%Y",
            "%b %d, %Y",
            "%Y/%m/%d",
        ]
        for f in fmts:
            try:
                return datetime.strptime(s, f).date()
            except Exception:
                pass
        # Extract date-like substrings (e.g., 2024-03-10)
        m = re.search(r"(\d{4}-\d{2}-\d{2})", s)
        if m:
            try:
                return datetime.strptime(m.group(1), "%Y-%m-%d").date()
            except Exception:
                pass
        return None

    def _parse_amount(self, v: Any) -> Optional[float]:
        if v is None:
            return None
        s = str(v)
        # Handle parentheses for negatives, strip currency and commas
        neg = "(" in s and ")" in s
        s = s.replace("$", "").replace(",", "").replace("(", "").replace(")", "").strip()
        # Remove any non-numeric trailing characters
        s = re.sub(r"[^0-9\.-]", "", s)
        try:
            num = float(s)
            return -num if neg else num
        except Exception:
            return None

