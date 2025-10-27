"""
Document processing service using pdfplumber

Responsibilities (Milestone B):
- Extract tables from PDF using pdfplumber
- Classify tables (capital calls, distributions, adjustments)
- Map rows to SQL models and insert into database
- Extract and chunk text for vector storage
- Handle errors and edge cases and return stats
"""
from typing import Dict, List, Any, Tuple
import pdfplumber
from sqlalchemy.orm import Session
from sqlalchemy import and_
from app.core.config import settings
from app.services.table_parser import TableParser
from app.services.vector_store import VectorStore
from app.db.session import SessionLocal
from app.models.transaction import CapitalCall, Distribution, Adjustment
from app.models.document import Document
import re

class DocumentProcessor:
    """Process PDF documents and extract structured data"""
    
    def __init__(self):
        self.table_parser = TableParser()
    
    async def process_document(self, file_path: str, document_id: int, fund_id: int) -> Dict[str, Any]:
        """
        Process a PDF document
        
        Args:
            file_path: Path to the PDF file
            document_id: Database document ID
            fund_id: Fund ID
            
        Returns:
            Processing result with statistics
        """
        db: Session = SessionLocal()
        vector_store = VectorStore(db)
        stats = {
            "status": "completed",
            "pages": 0,
            "tables_found": 0,
            "capital_calls_inserted": 0,
            "distributions_inserted": 0,
            "adjustments_inserted": 0,
            "text_chunks": 0,
        }

        try:
            # Validate document exists
            doc = db.query(Document).filter(Document.id == document_id).first()
            if not doc:
                return {"status": "failed", "error": "Document not found"}

            with pdfplumber.open(file_path) as pdf:
                stats["pages"] = len(pdf.pages)

                # Accumulate text per page for chunking
                text_contents: List[Dict[str, Any]] = []

                for page_index, page in enumerate(pdf.pages):
                    # Extract text
                    page_text = page.extract_text() or ""
                    if page_text.strip():
                        text_contents.append({
                            "page": page_index + 1,
                            "text": page_text,
                        })

                    # Extract tables
                    try:
                        tables = page.extract_tables()
                    except Exception:
                        tables = []

                    for table in tables or []:
                        if not table or len(table) < 2:
                            continue
                        headers = [self._clean_header(h) for h in (table[0] or [])]
                        # Skip if headers are empty
                        if not any(h for h in headers):
                            continue
                        rows = table[1:]
                        parsed = self.table_parser.parse(headers, rows)
                        table_type = parsed.get("type")
                        if not table_type:
                            continue

                        stats["tables_found"] += 1
                        # Insert into DB
                        if table_type == "capital_calls":
                            count = self._insert_capital_calls(db, fund_id, parsed["items"])
                            stats["capital_calls_inserted"] += count
                        elif table_type == "distributions":
                            count = self._insert_distributions(db, fund_id, parsed["items"])
                            stats["distributions_inserted"] += count
                        elif table_type == "adjustments":
                            count = self._insert_adjustments(db, fund_id, parsed["items"])
                            stats["adjustments_inserted"] += count

                # Chunk text and add to vector store
                chunks = self._chunk_text(text_contents)
                for idx, ch in enumerate(chunks):
                    metadata = {
                        "document_id": document_id,
                        "fund_id": fund_id,
                        "page": ch.get("page"),
                        "chunk_index": idx,
                    }
                    await vector_store.add_document(ch["content"], metadata)
                stats["text_chunks"] = len(chunks)

            return stats

        except Exception as e:
            # Log the error for visibility in tests/logs
            try:
                print(f"Document processing error: {e}")
            except Exception:
                pass
            db.rollback()
            return {"status": "failed", "error": str(e)}
        finally:
            db.close()
    
    def _chunk_text(self, text_content: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """
        Chunk text content for vector storage
        
        Args:
            text_content: List of text content with metadata
            
        Returns:
            List of text chunks with metadata
        """
        chunks: List[Dict[str, Any]] = []
        max_len = max(200, int(settings.CHUNK_SIZE))
        overlap = max(0, int(settings.CHUNK_OVERLAP))

        for item in text_content:
            text = (item.get("text") or "").strip()
            if not text:
                continue

            # Split into sentences (simple heuristic)
            sentences = self._split_sentences(text)

            buffer: List[str] = []
            curr_len = 0
            i = 0
            while i < len(sentences):
                s = sentences[i]
                if curr_len + len(s) + 1 <= max_len:
                    buffer.append(s)
                    curr_len += len(s) + 1
                    i += 1
                else:
                    if buffer:
                        content = " ".join(buffer).strip()
                        chunks.append({"content": content, "page": item.get("page")})
                        # Create overlap by rewinding sentences until overlap size is met
                        if overlap > 0:
                            rewind_text = ""
                            j = len(buffer) - 1
                            overlap_buffer: List[str] = []
                            while j >= 0 and len(rewind_text) < overlap:
                                overlap_buffer.insert(0, buffer[j])
                                rewind_text = (buffer[j] + " " + rewind_text).strip()
                                j -= 1
                            buffer = overlap_buffer
                            curr_len = len(" ".join(buffer))
                        else:
                            buffer = []
                            curr_len = 0
                    else:
                        # Handle very long single sentence
                        chunks.append({"content": s[:max_len], "page": item.get("page")})
                        i += 1

            if buffer:
                content = " ".join(buffer).strip()
                if content:
                    chunks.append({"content": content, "page": item.get("page")})

        return chunks

    # ------------------------
    # Insertion helpers
    # ------------------------
    def _insert_capital_calls(self, db: Session, fund_id: int, items: List[Dict[str, Any]]) -> int:
        inserted = 0
        for it in items:
            if it.get("call_date") is None or it.get("amount") is None:
                continue
            # Deduplicate by (fund_id, date, amount)
            exists = db.query(CapitalCall).filter(
                and_(
                    CapitalCall.fund_id == fund_id,
                    CapitalCall.call_date == it["call_date"],
                    CapitalCall.amount == it["amount"],
                )
            ).first()
            if exists:
                continue
            db.add(CapitalCall(
                fund_id=fund_id,
                call_date=it["call_date"],
                call_type=it.get("call_type"),
                amount=it["amount"],
                description=it.get("description"),
            ))
            inserted += 1
        db.commit()
        return inserted

    def _insert_distributions(self, db: Session, fund_id: int, items: List[Dict[str, Any]]) -> int:
        inserted = 0
        for it in items:
            if it.get("distribution_date") is None or it.get("amount") is None:
                continue
            exists = db.query(Distribution).filter(
                and_(
                    Distribution.fund_id == fund_id,
                    Distribution.distribution_date == it["distribution_date"],
                    Distribution.amount == it["amount"],
                )
            ).first()
            if exists:
                continue
            db.add(Distribution(
                fund_id=fund_id,
                distribution_date=it["distribution_date"],
                distribution_type=it.get("distribution_type"),
                is_recallable=bool(it.get("is_recallable", False)),
                amount=it["amount"],
                description=it.get("description"),
            ))
            inserted += 1
        db.commit()
        return inserted

    def _insert_adjustments(self, db: Session, fund_id: int, items: List[Dict[str, Any]]) -> int:
        inserted = 0
        for it in items:
            if it.get("adjustment_date") is None or it.get("amount") is None:
                continue
            exists = db.query(Adjustment).filter(
                and_(
                    Adjustment.fund_id == fund_id,
                    Adjustment.adjustment_date == it["adjustment_date"],
                    Adjustment.amount == it["amount"],
                )
            ).first()
            if exists:
                continue
            db.add(Adjustment(
                fund_id=fund_id,
                adjustment_date=it["adjustment_date"],
                adjustment_type=it.get("adjustment_type"),
                category=it.get("category"),
                is_contribution_adjustment=bool(it.get("is_contribution_adjustment", False)),
                amount=it["amount"],
                description=it.get("description"),
            ))
            inserted += 1
        db.commit()
        return inserted

    # ------------------------
    # Text helpers
    # ------------------------
    def _split_sentences(self, text: str) -> List[str]:
        # Simple sentence tokenizer preserving delimiters
        parts = re.split(r"([\.\!\?])", text)
        out: List[str] = []
        for i in range(0, len(parts), 2):
            s = parts[i].strip()
            if not s:
                continue
            delim = parts[i + 1] if i + 1 < len(parts) else ""
            out.append((s + delim).strip())
        return out

    def _clean_header(self, h: Any) -> str:
        if h is None:
            return ""
        return str(h).strip()
