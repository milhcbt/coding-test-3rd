import pytest
from sqlalchemy.orm import Session
from decimal import Decimal
from datetime import date

from app.services.metrics_calculator import MetricsCalculator
from app.models.fund import Fund
from app.models.transaction import CapitalCall, Distribution, Adjustment


@pytest.fixture()
def seed_fund(db: Session):
    fund = Fund(name="Test Fund")
    db.add(fund)
    db.commit()
    db.refresh(fund)

    # Calls: 10,000,000
    db.add_all([
        CapitalCall(fund_id=fund.id, call_date=date(2023, 1, 15), amount=Decimal("5000000"), description="Initial"),
        CapitalCall(fund_id=fund.id, call_date=date(2023, 6, 20), amount=Decimal("3000000"), description="Follow-on"),
        CapitalCall(fund_id=fund.id, call_date=date(2024, 3, 10), amount=Decimal("2000000"), description="Bridge"),
    ])

    # Distributions: 4,000,000
    db.add_all([
        Distribution(fund_id=fund.id, distribution_date=date(2023, 12, 15), amount=Decimal("1500000"), description="Exit A"),
        Distribution(fund_id=fund.id, distribution_date=date(2024, 6, 20), amount=Decimal("500000"), description="Dividend"),
        Distribution(fund_id=fund.id, distribution_date=date(2024, 9, 10), amount=Decimal("2000000"), description="Partial Exit B", is_recallable=True),
    ])

    # Adjustments: -400,000 + 100,000 = -300,000 (net)
    db.add_all([
        Adjustment(fund_id=fund.id, adjustment_date=date(2024, 1, 15), amount=Decimal("-500000"), adjustment_type="Recallable Dist", category="Rebalance of Distribution"),
        Adjustment(fund_id=fund.id, adjustment_date=date(2024, 3, 20), amount=Decimal("100000"), adjustment_type="Capital Call Adj", category="Capital"),
    ])

    db.commit()
    return fund


def test_metrics_calculation(db: Session, seed_fund: Fund):
    calc = MetricsCalculator(db)
    metrics = calc.calculate_all_metrics(seed_fund.id)

    # Adjustments sum to -400,000 (from -500k + 100k); PIC = calls - (-400k) = 10,400,000
    assert metrics["pic"] == pytest.approx(10_400_000, abs=1e-6)
    assert metrics["total_distributions"] == pytest.approx(4_000_000, abs=1e-6)
    assert metrics["dpi"] == pytest.approx(4_000_000 / 10_400_000, rel=1e-3)

    irr = calc.calculate_irr(seed_fund.id)
    assert irr is not None
