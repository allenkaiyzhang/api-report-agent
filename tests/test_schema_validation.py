"""Tests for JSON schema validation."""

from __future__ import annotations

import json
import unittest
from pathlib import Path

import jsonschema


_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "config" / "schemas"


def _load_schema(name: str) -> dict:
    path = _SCHEMA_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Schema not found: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


class TestQuoteSchema(unittest.TestCase):
    """Validate quote.schema.json."""

    def setUp(self):
        self.schema = _load_schema("quote.schema.json")

    def test_valid_quote(self):
        data = {
            "symbol": "QQQ",
            "market": "US",
            "latest_price": 445.20,
            "previous_close": 436.10,
            "change_percent": 2.09,
            "open": 436.50,
            "high": 446.00,
            "low": 435.00,
            "volume": 63000000,
            "turnover": 28035000000.0,
            "bid": 445.15,
            "ask": 445.25,
            "trade_status": "normal",
            "currency": "USD",
            "timestamp": "2026-06-06T10:00:00Z",
            "event_time": "2026-06-06T10:00:00Z",
            "source": "mock",
        }
        jsonschema.validate(data, self.schema)

    def test_missing_required_field(self):
        data = {"symbol": "QQQ", "market": "US", "timestamp": "2026-06-06T10:00:00Z"}
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)

    def test_invalid_market(self):
        data = {
            "symbol": "QQQ",
            "market": "JP",
            "latest_price": 445.20,
            "timestamp": "2026-06-06T10:00:00Z",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)

    def test_negative_price(self):
        data = {
            "symbol": "QQQ",
            "market": "US",
            "latest_price": -10.0,
            "timestamp": "2026-06-06T10:00:00Z",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)


class TestCandleSchema(unittest.TestCase):
    """Validate candle.schema.json."""

    def setUp(self):
        self.schema = _load_schema("candle.schema.json")

    def test_valid_candle(self):
        data = {
            "symbol": "QQQ",
            "market": "US",
            "close": 445.0,
            "open": 440.0,
            "high": 446.0,
            "low": 438.0,
            "volume": 50000000,
            "turnover": 22250000000.0,
            "timestamp": "2026-06-06",
            "trade_session": "regular",
            "source": "mock",
        }
        jsonschema.validate(data, self.schema)

    def test_missing_close(self):
        data = {
            "symbol": "QQQ",
            "market": "US",
            "open": 440.0,
            "high": 446.0,
            "low": 438.0,
            "timestamp": "2026-06-06",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)


class TestIntradaySchema(unittest.TestCase):
    """Validate intraday.schema.json."""

    def setUp(self):
        self.schema = _load_schema("intraday.schema.json")

    def test_valid_intraday(self):
        data = {
            "symbol": "QQQ",
            "market": "US",
            "price": 445.0,
            "volume": 1000000,
            "turnover": 445000000.0,
            "timestamp": "2026-06-06T10:00:00Z",
            "source": "mock",
        }
        jsonschema.validate(data, self.schema)

    def test_missing_price(self):
        data = {
            "symbol": "QQQ",
            "market": "US",
            "timestamp": "2026-06-06T10:00:00Z",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)


class TestMarketStatusSchema(unittest.TestCase):
    """Validate market_status.schema.json."""

    def setUp(self):
        self.schema = _load_schema("market_status.schema.json")

    def test_valid_status(self):
        data = {
            "market": "US",
            "is_open": True,
            "session": "regular",
            "next_open": "",
            "next_close": "",
            "timestamp": "2026-06-06T10:00:00Z",
            "source": "mock",
        }
        jsonschema.validate(data, self.schema)

    def test_invalid_session(self):
        data = {
            "market": "US",
            "is_open": True,
            "session": "invalid_session",
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)


class TestMarketReportDatasetSchema(unittest.TestCase):
    """Validate market_report_dataset.schema.json."""

    def setUp(self):
        self.schema = _load_schema("market_report_dataset.schema.json")

    def test_valid_dataset(self):
        data = {
            "run_id": "test-001",
            "report_type": "intraday_brief",
            "market": "US",
            "symbols": ["QQQ"],
            "quotes": [],
            "candles": [],
            "intraday": [],
            "market_status": {
                "market": "US",
                "is_open": True,
                "session": "regular"
            },
            "fundamentals": [],
            "collected_at": "2026-06-06T10:00:00Z",
            "validated": False,
            "validation_errors": [],
        }
        jsonschema.validate(data, self.schema)

    def test_missing_run_id(self):
        data = {
            "report_type": "intraday_brief",
            "market": "US",
            "symbols": [],
            "collected_at": "2026-06-06T10:00:00Z",
            "validated": False,
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)

    def test_invalid_report_type(self):
        data = {
            "run_id": "test-001",
            "report_type": "unknown_type",
            "market": "US",
            "symbols": [],
            "collected_at": "2026-06-06T10:00:00Z",
            "validated": False,
        }
        with self.assertRaises(jsonschema.ValidationError):
            jsonschema.validate(data, self.schema)


if __name__ == "__main__":
    unittest.main()
