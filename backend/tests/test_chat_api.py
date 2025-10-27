import pytest
from fastapi.testclient import TestClient
from decimal import Decimal
from datetime import date

from app.main import app
from app.models.fund import Fund
from app.models.transaction import CapitalCall, Distribution, Adjustment


@pytest.fixture()
def client():
    return TestClient(app)


@pytest.fixture()
def seeded_fund(db):
    fund = Fund(name="Chat Fund")
    db.add(fund)
    db.commit()
    db.refresh(fund)

    # Capital calls: 10,000,000
    db.add_all([
        CapitalCall(fund_id=fund.id, call_date=date(2023, 1, 15), amount=Decimal("5000000")),
        CapitalCall(fund_id=fund.id, call_date=date(2023, 6, 20), amount=Decimal("3000000")),
        CapitalCall(fund_id=fund.id, call_date=date(2024, 3, 10), amount=Decimal("2000000")),
    ])
    # Distributions: 4,000,000
    db.add_all([
        Distribution(fund_id=fund.id, distribution_date=date(2023, 12, 15), amount=Decimal("1500000")),
        Distribution(fund_id=fund.id, distribution_date=date(2024, 6, 20), amount=Decimal("500000")),
        Distribution(fund_id=fund.id, distribution_date=date(2024, 9, 10), amount=Decimal("2000000"), is_recallable=True),
    ])
    # Adjustments: net -400,000
    db.add_all([
        Adjustment(fund_id=fund.id, adjustment_date=date(2024, 1, 15), amount=Decimal("-500000"), adjustment_type="Recallable Dist"),
        Adjustment(fund_id=fund.id, adjustment_date=date(2024, 3, 20), amount=Decimal("100000"), adjustment_type="Capital Call Adj"),
    ])
    db.commit()
    return fund


@pytest.mark.asyncio
async def test_chat_query_metrics_shortcut(monkeypatch, client, seeded_fund):
    # Stub vector search to avoid embeddings/DB vector ops
    from app.services import query_engine as qe_mod

    async def fake_search(self, query: str, k: int = 5, filter_metadata=None):
        return [{
            "content": "DPI is Distributions to Paid-In.",
            "metadata": {"fund_id": seeded_fund.id},
            "score": 0.9,
        }]

    async def fake_generate(self, query, context, metrics, conversation_history):
        # Deterministic response that includes DPI value
        dpi = metrics.get("dpi") if metrics else None
        return f"Current DPI is {dpi}."

    def fake_init_llm(self):
        class Dummy:
            def invoke(self, *args, **kwargs):
                return type("R", (), {"content": "dummy"})()
        return Dummy()

    monkeypatch.setattr(qe_mod.VectorStore, "similarity_search", fake_search, raising=False)
    monkeypatch.setattr(qe_mod.QueryEngine, "_generate_response", fake_generate, raising=False)
    monkeypatch.setattr(qe_mod.QueryEngine, "_initialize_llm", fake_init_llm, raising=False)

    payload = {"query": "What is the current DPI?", "fund_id": seeded_fund.id}
    res = client.post("/api/chat/query", json=payload)

    assert res.status_code == 200
    data = res.json()

    # Should include metrics shortcut results
    assert "metrics" in data and isinstance(data["metrics"], dict)
    assert data["metrics"]["dpi"] > 0

    # Deterministic answer from fake_generate
    assert data["answer"].startswith("Current DPI is ")

    # Sources are from stubbed vector search
    assert len(data.get("sources", [])) >= 1
