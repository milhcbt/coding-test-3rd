import pytest
from app.services.table_parser import TableParser


def test_classify_and_parse_capital_calls():
    headers = ["Date", "Call Number", "Amount", "Description"]
    rows = [
        ["2024-03-10", "Call 3", "$2,000,000", "Bridge Round"],
        ["2024/03/20", "Call 4", "(100000)", "Fee Adjustment"],
    ]

    parser = TableParser()
    result = parser.parse(headers, rows)

    assert result["type"] == "capital_calls"
    assert len(result["items"]) == 2
    item = result["items"][0]
    assert item["call_date"] is not None
    assert item["amount"] == 2000000.0


def test_classify_and_parse_distributions():
    headers = ["Date", "Type", "Amount", "Recallable", "Description"]
    rows = [
        ["2023-12-15", "Return", "$1,500,000", "No", "Exit A"],
        ["2024-09-10", "Return", "$2,000,000", "Yes", "Partial Exit B"],
    ]

    parser = TableParser()
    result = parser.parse(headers, rows)

    assert result["type"] == "distributions"
    assert len(result["items"]) == 2
    assert result["items"][1]["is_recallable"] is True


def test_classify_and_parse_adjustments():
    headers = ["Date", "Type", "Category", "Amount", "Description"]
    rows = [
        ["2024-01-15", "Recallable Dist", "Rebalance of Distribution", "-500000", "Recalled"],
        ["2024-03-20", "Capital Call Adj", "Capital", "100000", "Fee adj"],
    ]

    parser = TableParser()
    result = parser.parse(headers, rows)

    assert result["type"] == "adjustments"
    assert len(result["items"]) == 2
    # Contribution adjustment flagged
    assert result["items"][1]["is_contribution_adjustment"] is True
