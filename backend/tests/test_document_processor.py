import pytest
import types
from datetime import date

from app.services.document_processor import DocumentProcessor
from app.models.fund import Fund
from app.models.document import Document
from app.models.transaction import CapitalCall, Distribution, Adjustment


class FakePage:
    def __init__(self, text, tables):
        self._text = text
        self._tables = tables

    def extract_text(self):
        return self._text

    def extract_tables(self):
        return self._tables


class FakePDF:
    def __init__(self, pages):
        self.pages = pages

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return False


class FakeVectorStore:
    def __init__(self, *args, **kwargs):
        self.docs = []

    async def add_document(self, content: str, metadata: dict):
        self.docs.append((content, metadata))


@pytest.mark.asyncio
async def test_document_processor_inserts_transactions(db, fake_pdf_path, monkeypatch, adjust_settings):
    # Seed a fund and a document
    fund = Fund(name="Test Fund")
    db.add(fund)
    db.commit()
    db.refresh(fund)

    doc = Document(fund_id=fund.id, file_name="sample.pdf", file_path=fake_pdf_path, parsing_status="processing")
    db.add(doc)
    db.commit()
    db.refresh(doc)

    # Prepare fake pdf with 1 page, 3 tables and some text
    headers_calls = ["Date", "Call Number", "Amount", "Description"]
    rows_calls = [
        ["2023-01-15", "Call 1", "$5,000,000", "Initial Capital"],
        ["2023-06-20", "Call 2", "$3,000,000", "Follow-on"],
    ]

    headers_dists = ["Date", "Type", "Amount", "Recallable", "Description"]
    rows_dists = [
        ["2023-12-15", "Return", "$1,500,000", "No", "Exit: A"],
        ["2024-06-20", "Income", "$500,000", "No", "Dividend"],
    ]

    headers_adjs = ["Date", "Type", "Category", "Amount", "Description"]
    rows_adjs = [
        ["2024-01-15", "Recallable Dist", "Rebalance of Distribution", "-500000", "Recalled"],
    ]

    fake_pages = [
        FakePage(
            text=(
                "Paid-In Capital (PIC) is the total capital contributions minus adjustments.\n"
                "DPI = Cumulative Distributions / PIC."
            ),
            tables=[
                headers_calls + [],
                # pdfplumber returns tables as list[list]; include header row as first inner list
            ],
        )
    ]

    # pdfplumber returns tables as list of rows; adjust to proper structure per page
    fake_pages[0]._tables = [
        [headers_calls] + rows_calls,
        [headers_dists] + rows_dists,
        [headers_adjs] + rows_adjs,
    ]

    def fake_open(_path):
        return FakePDF(fake_pages)

    # Monkeypatch pdfplumber.open and VectorStore to prevent external deps
    import app.services.document_processor as dp_mod
    monkeypatch.setattr(dp_mod, "pdfplumber", types.SimpleNamespace(open=fake_open))
    monkeypatch.setattr(dp_mod, "VectorStore", FakeVectorStore)

    processor = DocumentProcessor()
    result = await processor.process_document(fake_pdf_path, doc.id, fund.id)

    # Validate stats
    print("PROCESS_RESULT:", result)
    assert result["status"] == "completed"
    assert result["tables_found"] == 3
    assert result["capital_calls_inserted"] == 2
    assert result["distributions_inserted"] == 2
    assert result["adjustments_inserted"] == 1
    assert result["text_chunks"] >= 1

    # Validate DB contents
    assert db.query(CapitalCall).filter(CapitalCall.fund_id == fund.id).count() == 2
    assert db.query(Distribution).filter(Distribution.fund_id == fund.id).count() == 2
    assert db.query(Adjustment).filter(Adjustment.fund_id == fund.id).count() == 1
