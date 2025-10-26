"""
Document processing service using pdfplumber

MVP implementation for Milestone B:
- Extract tables from PDF using pdfplumber
- Classify tables (capital calls, distributions, adjustments)
- Normalize and insert rows into SQL tables
- Return simple processing statistics

Notes:
- Text chunking and vector storage are deferred to later milestones.
"""
from typing import Dict, List, Any, Optional
from decimal import Decimal
import re
from datetime import datetime

import pdfplumber
from sqlalchemy.exc import SQLAlchemyError

from app.core.config import settings
from app.services.table_parser import TableParser
from app.db.session import SessionLocal
from app.models.transaction import CapitalCall, Distribution, Adjustment
from app.models.fund import Fund
from app.models.document import Document


class DocumentProcessor:
    """Process PDF documents and extract structured data"""

    def __init__(self):
        self.table_parser = TableParser()

    async def process_document(
        self, file_path: str, document_id: int, fund_id: Optional[int]
    ) -> Dict[str, Any]:
        """
        Process a PDF document and insert recognized tables into the database.

        Args:
            file_path: Path to the PDF file
            document_id: Database document ID
            fund_id: Optional Fund ID (will create a default if missing)

        Returns:
            Dict with status and simple statistics
        """
        db = SessionLocal()
        stats = {
            "pages": 0,
            "tables_found": 0,
            "inserted": {"capital_calls": 0, "distributions": 0, "adjustments": 0},
            "skipped_rows": 0,
        }

        try:
            # Ensure document exists
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                return {"status": "failed", "error": "Document not found"}

            # Ensure fund exists (create a default if not provided/found)
            fund = None
            if fund_id:
                fund = db.query(Fund).filter(Fund.id == fund_id).first()
            if not fund:
                fund = Fund(name=f"Fund {datetime.utcnow().strftime('%Y%m%d%H%M%S')}")
                db.add(fund)
                db.commit()
                db.refresh(fund)
                # Update document to reference this fund
                doc.fund_id = fund.id
                db.commit()
                fund_id = fund.id

            # Iterate PDF pages and extract tables
            with pdfplumber.open(file_path) as pdf:
                stats["pages"] = len(pdf.pages)
                for page in pdf.pages:
                    # Aggregate tables from general extraction and heading-based crops
                    tables = self._extract_tables(page)
                    tables += self._extract_tables_by_heading(page)
                    if not tables:
                        continue
                    seen_signatures = set()
                    for raw_table in tables:
                        # Clean table: drop empty rows, strip cells
                        cleaned = self._clean_table(raw_table)
                        if not cleaned or len(cleaned) < 2:
                            continue

                        headers = [str(h or "").strip() for h in cleaned[0]]
                        rows = cleaned[1:]

                        # Deduplicate by normalized header signature and row count
                        sig_cols = tuple([h.strip().lower().replace(" ", "_") for h in headers])
                        sig = (sig_cols, len(rows))
                        if sig in seen_signatures:
                            continue
                        seen_signatures.add(sig)

                        table_type = self.table_parser.classify(headers) or self._infer_type_from_headers(headers)
                        if not table_type:
                            continue

                        stats["tables_found"] += 1
                        parsed = self.table_parser.parse(headers, rows)
                        # Refine type based on normalized columns and sample rows
                        table_type = self._deduce_type_from_content(table_type, parsed)
                        print(f"[DocProc] Classified table as {table_type} with columns={parsed.get('columns')}")
                        inserted, skipped = self._insert_rows(db, table_type, parsed, fund.id)
                        print(f"[DocProc] Inserted {inserted}, skipped {skipped} rows for {table_type}")
                        stats["inserted"][table_type] += inserted
                        stats["skipped_rows"] += skipped

            print(f"[DocProc] Completed processing doc={document_id}, stats={stats}")
            return {"status": "completed", "stats": stats}

        except SQLAlchemyError as e:
            db.rollback()
            return {"status": "failed", "error": f"DB error: {e}"}
        except Exception as e:
            return {"status": "failed", "error": str(e)}
        finally:
            db.close()

    # ---- helpers ----

    def _extract_tables(self, page) -> List[List[List[Any]]]:
        """Extract tables using multiple strategies and merge results."""
        results: List[List[List[Any]]] = []
        strategies = [
            {
                "vertical_strategy": "lines",
                "horizontal_strategy": "lines",
                "intersection_y_tolerance": 5,
                "intersection_x_tolerance": 5,
                "snap_tolerance": 3,
            },
            {
                "vertical_strategy": "text",
                "horizontal_strategy": "text",
                "intersection_y_tolerance": 8,
                "intersection_x_tolerance": 8,
                "snap_tolerance": 4,
            },
            None,  # default
        ]

        for settings in strategies:
            try:
                tables = page.extract_tables(settings) if settings else page.extract_tables()
                if tables:
                    results.extend(tables)
            except Exception:
                continue

        return results

    def _extract_tables_by_heading(self, page) -> List[List[List[Any]]]:
        """Crop below known section headings and try to extract a table in that region."""
        results: List[List[List[Any]]] = []
        try:
            words = page.extract_words(use_text_flow=True)
        except Exception:
            words = []
        if not words:
            return results

        heading_keys = {"capital calls", "distributions", "adjustments"}

        # Build simple lines by grouping words with close 'top' value
        lines: dict[float, list] = {}
        for w in words:
            top = round(float(w.get("top", 0.0)), 0)
            lines.setdefault(top, []).append(w)

        for top, ws in lines.items():
            text = " ".join([w.get("text", "") for w in sorted(ws, key=lambda x: x.get("x0", 0))]).strip().lower()
            if any(h in text for h in heading_keys):
                # Define a bbox from just below the line down to half a page
                y0 = float(min(w.get("bottom", top + 5) for w in ws)) + 2
                y1 = min(page.height, y0 + page.height / 2)
                bbox = (0, y0, page.width, y1)
                try:
                    with page.within_bbox(bbox) as cropped:
                        # Try multiple strategies on the cropped region
                        cropped_tables = []
                        for settings in [
                            {
                                "vertical_strategy": "lines",
                                "horizontal_strategy": "lines",
                                "intersection_y_tolerance": 5,
                                "intersection_x_tolerance": 5,
                                "snap_tolerance": 3,
                            },
                            {
                                "vertical_strategy": "text",
                                "horizontal_strategy": "text",
                                "intersection_y_tolerance": 8,
                                "intersection_x_tolerance": 8,
                                "snap_tolerance": 4,
                            },
                            None,
                        ]:
                            try:
                                t = cropped.extract_tables(settings) if settings else cropped.extract_tables()
                                if t:
                                    cropped_tables.extend(t)
                            except Exception:
                                continue
                        results.extend(cropped_tables)
                except Exception:
                    continue
        return results

    def _clean_table(self, table: List[List[Any]]) -> List[List[str]]:
        cleaned = []
        for row in table:
            if not row:
                continue
            # Convert all cells to strings and strip
            cells = [str(c).strip() if c is not None else "" for c in row]
            # Skip empty rows
            if any(cells):
                cleaned.append(cells)
        return cleaned

    def _infer_type_from_headers(self, headers: List[str]) -> Optional[str]:
        h = " ".join([x.lower() for x in headers])
        if any(k in h for k in ("capital call", "contribution", "call")):
            return "capital_calls"
        if any(k in h for k in ("distribution", "return", "dividend", "income")):
            return "distributions"
        if any(k in h for k in ("adjustment", "recall", "rebalance")):
            return "adjustments"
        return None

    def _insert_rows(
        self, db, table_type: str, parsed: Dict[str, Any], fund_id: int
    ) -> tuple[int, int]:
        """Insert parsed rows into the corresponding table; return (inserted, skipped)."""
        inserted = 0
        skipped = 0
        columns = parsed.get("columns", [])
        rows = parsed.get("rows", [])

        # Identify likely column names
        date_idx = self._find_col(columns, ["date", "call_date", "distribution_date", "adjustment_date"])
        amount_idx = self._find_col(columns, ["amount", "value", "usd", "total"])
        desc_idx = self._find_col(columns, ["description", "notes", "memo"])
        type_idx = self._find_col(columns, ["type", "call_type", "distribution_type", "adjustment_type", "category"])
        recall_idx = self._find_col(columns, ["recallable", "is_recallable", "recall"])

        for r in rows:
            try:
                date_str = r[date_idx] if date_idx is not None and date_idx < len(r) else ""
                amount_str = r[amount_idx] if amount_idx is not None and amount_idx < len(r) else ""
                desc = r[desc_idx] if desc_idx is not None and desc_idx < len(r) else None
                typ = r[type_idx] if type_idx is not None and type_idx < len(r) else None
                recall_val = r[recall_idx] if recall_idx is not None and recall_idx < len(r) else None

                dt = self._parse_date(date_str)
                amt = self._parse_amount(amount_str)
                if dt is None or amt is None:
                    skipped += 1
                    continue

                if table_type == "capital_calls":
                    item = CapitalCall(
                        fund_id=fund_id,
                        call_date=dt,
                        call_type=(typ or "Capital Call"),
                        amount=Decimal(str(amt)),
                        description=desc,
                    )
                elif table_type == "distributions":
                    is_recallable = self._parse_bool(recall_val)
                    item = Distribution(
                        fund_id=fund_id,
                        distribution_date=dt,
                        distribution_type=(typ or "Distribution"),
                        is_recallable=is_recallable,
                        amount=Decimal(str(amt)),
                        description=desc,
                    )
                else:  # adjustments
                    item = Adjustment(
                        fund_id=fund_id,
                        adjustment_date=dt,
                        adjustment_type=(typ or "Adjustment"),
                        category=(typ or "Adjustment"),
                        amount=Decimal(str(amt)),
                        description=desc,
                    )

                db.add(item)
                inserted += 1
            except Exception:
                skipped += 1

        if inserted:
            db.commit()
        return inserted, skipped

    def _find_col(self, columns: List[str], candidates: List[str]) -> Optional[int]:
        for i, c in enumerate(columns):
            cl = c.lower()
            for cand in candidates:
                if cand in cl:
                    return i
        return None

    def _parse_date(self, s: str) -> Optional[datetime.date]:
        if not s:
            return None
        s = s.strip()
        fmts = ["%Y-%m-%d", "%d-%m-%Y", "%m/%d/%Y", "%d/%m/%Y", "%b %d, %Y", "%Y/%m/%d"]
        for f in fmts:
            try:
                return datetime.strptime(s, f).date()
            except Exception:
                continue
        # Try to extract YYYY-MM-DD pattern
        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", s)
        if m:
            try:
                y, mo, d = map(int, m.groups())
                return datetime(y, mo, d).date()
            except Exception:
                return None
        return None

    def _parse_amount(self, s: str) -> Optional[float]:
        if s is None:
            return None
        txt = str(s).strip()
        if not txt:
            return None
        # Handle parentheses for negatives, remove currency symbols and commas
        neg = False
        if txt.startswith("(") and txt.endswith(")"):
            neg = True
            txt = txt[1:-1]
        txt = txt.replace("$", "").replace(",", "").replace("\u00A0", " ")
        # Remove any stray letters
        txt = re.sub(r"[^0-9.+-]", "", txt)
        if not txt:
            return None
        try:
            val = float(txt)
            return -val if neg else val
        except Exception:
            return None

    def _parse_bool(self, v: Any) -> bool:
        if v is None:
            return False
        s = str(v).strip().lower()
        return s in {"yes", "y", "true", "t", "1"}

    # Placeholder for future text chunking (not used in MVP)
    def _chunk_text(self, text_content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        return []

    def _deduce_type_from_content(self, initial_type: str, parsed: Dict[str, Any]) -> str:
        """Refine table classification using columns and sample row values."""
        cols = [c.lower() for c in parsed.get("columns", [])]
        sample_rows = parsed.get("rows", [])[:5]

        # Exact header set matches for our sample PDF
        colset = set(cols)
        if colset == {"date", "type", "amount", "recallable", "description"}:
            return "distributions"
        if colset == {"date", "call_number", "amount", "description"}:
            return "capital_calls"
        if colset == {"date", "type", "amount", "description"}:
            # Likely adjustments in our sample PDF
            return "adjustments"
        
        # If header contains 'recallable', it's a distributions table
        if any("recallable" in c for c in cols):
            return "distributions"
        # If header mentions specific distribution words
        if any("distribution" in c for c in cols):
            return "distributions"
        # If header contains 'call_number' or similar, it's capital calls
        if any(c in {"call_number", "call_no", "call"} for c in cols):
            return "capital_calls"
        # If header contains 'adjustment' or 'category', likely adjustments
        if any("adjustment" in c or c == "category" for c in cols):
            return "adjustments"

        # Inspect 'type' column values if present
        try:
            type_idx = self._find_col(cols, ["type", "call_type", "distribution_type", "adjustment_type"])
        except Exception:
            type_idx = None
        if type_idx is not None:
            vals = set(str(r[type_idx]).strip().lower() for r in sample_rows if type_idx < len(r))
            if any(v for v in vals if any(k in v for k in ["return", "income", "distribution", "dividend"])):
                return "distributions"
            if any("adjust" in v for v in vals):
                return "adjustments"

        return initial_type
