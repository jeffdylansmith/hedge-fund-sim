"""Tests for agents/options/options_executor.py"""

import pytest
from unittest.mock import patch, MagicMock
from agents.options.options_executor import (
    _option_symbol,
    _expiration_date_from_days,
    _calculate_unrealized_pnl,
    execute_options_trade,
)
from agents.options.position_monitor import (
    _calculate_unrealized_pnl,
    _should_close,
    run_position_monitor,
)
from agents.trader_config import ALEX
from datetime import date, datetime, timedelta


class TestOptionSymbol:

    def test_call_symbol_format(self):
        # Use NVDA (no P or C in ticker) so "P" not in symbol reliably checks put indicator
        symbol = _option_symbol("NVDA", "2025-12-19", 150.0, "call")
        assert symbol.startswith("NVDA")
        assert "C" in symbol
        assert "P" not in symbol

    def test_put_symbol_format(self):
        symbol = _option_symbol("AAPL", "2025-12-19", 150.0, "put")
        assert "P" in symbol

    def test_strike_encoded_correctly(self):
        # Strike 150.0 → 150000 → zero-padded to 8 digits → "00150000"
        symbol = _option_symbol("AAPL", "2025-12-19", 150.0, "call")
        assert "00150000" in symbol

    def test_strike_with_decimal(self):
        # Strike 152.5 → 152500
        symbol = _option_symbol("NVDA", "2025-12-19", 152.5, "put")
        assert "00152500" in symbol

    def test_ticker_at_start(self):
        symbol = _option_symbol("MSFT", "2025-06-20", 400.0, "call")
        assert symbol.startswith("MSFT")

    def test_date_encoded_as_yymmdd(self):
        symbol = _option_symbol("AAPL", "2025-12-19", 150.0, "call")
        assert "251219" in symbol


class TestExpirationDateFromDays:

    def test_returns_a_friday(self):
        exp = _expiration_date_from_days(30)
        dt = datetime.strptime(exp, "%Y-%m-%d")
        assert dt.weekday() == 4  # Friday

    def test_returns_future_date(self):
        exp = _expiration_date_from_days(14)
        dt = datetime.strptime(exp, "%Y-%m-%d").date()
        assert dt > date.today()

    def test_longer_duration_later_date(self):
        exp_30 = datetime.strptime(_expiration_date_from_days(30), "%Y-%m-%d").date()
        exp_60 = datetime.strptime(_expiration_date_from_days(60), "%Y-%m-%d").date()
        assert exp_60 > exp_30


class TestCalculateUnrealizedPnl:

    def test_profitable_long_call(self):
        position = {
            "contracts": 1,
            "net_credit_debit": -3.50,
            "entry_price": 3.50,
        }
        # Option now worth $5.00
        pnl = _calculate_unrealized_pnl(position, 5.00)
        assert pnl == pytest.approx(150.0)  # ($5 - $3.50) × 100

    def test_losing_long_call(self):
        position = {
            "contracts": 1,
            "net_credit_debit": -3.50,
            "entry_price": 3.50,
        }
        pnl = _calculate_unrealized_pnl(position, 1.50)
        assert pnl == pytest.approx(-200.0)  # ($1.50 - $3.50) × 100

    def test_none_price_returns_zero(self):
        position = {"contracts": 1, "net_credit_debit": -3.50, "entry_price": 3.50}
        assert _calculate_unrealized_pnl(position, None) == 0.0

    def test_scales_with_contracts(self):
        position = {"contracts": 3, "net_credit_debit": -3.50, "entry_price": 3.50}
        pnl = _calculate_unrealized_pnl(position, 5.00)
        assert pnl == pytest.approx(450.0)  # ($5 - $3.50) × 100 × 3


class TestShouldClose:

    def test_expired_position_closes(self):
        position = {
            "expiration_date": "2020-01-01",  # past date
            "max_loss_total": 500.0,
            "net_credit_debit": 0.0,
            "contracts": 1,
        }
        should, reason = _should_close(position, 0.0, date.today())
        assert should is True
        assert reason == "expired"

    def test_stop_loss_triggers(self):
        position = {
            "expiration_date": (date.today() + timedelta(days=20)).isoformat(),
            "max_loss_total": 500.0,
            "net_credit_debit": 0.0,
            "contracts": 1,
        }
        # Loss of 2x max_loss = stop loss
        should, reason = _should_close(position, -1001.0, date.today())
        assert should is True
        assert reason == "stop_loss"

    def test_profit_target_triggers_for_credit_spread(self):
        position = {
            "expiration_date": (date.today() + timedelta(days=20)).isoformat(),
            "max_loss_total": 400.0,
            "net_credit_debit": 2.00,  # credit received
            "contracts": 2,
        }
        # 50% of max profit = 50% of (2.00 × 100 × 2) = $200
        should, reason = _should_close(position, 201.0, date.today())
        assert should is True
        assert reason == "profit_target"

    def test_healthy_position_stays_open(self):
        position = {
            "expiration_date": (date.today() + timedelta(days=20)).isoformat(),
            "max_loss_total": 500.0,
            "net_credit_debit": 0.0,
            "contracts": 1,
        }
        should, reason = _should_close(position, -50.0, date.today())
        assert should is False


class TestExecuteOptionsTradedrRun:
    """
    Dry run tests — no Alpaca calls, no DB writes.
    All execution tests use dry_run=True.
    """

    def test_dry_run_long_call_succeeds(self):
        result = execute_options_trade(
            trader_id="alex",
            ticker="AAPL",
            strategy="long_call",
            strikes={"strike": 150.0, "expiration_days": 30},
            greeks={"price": 3.50, "delta": 0.45, "theta": -0.04,
                    "vega": 0.12, "gamma": 0.02},
            risk_assessment={"contracts": 2, "max_loss_total": 700.0},
            dry_run=True,
        )
        assert result["success"] is True
        assert result["dry_run"] is True
        assert result["contracts"] == 2
        assert result["ticker"] == "AAPL"

    def test_dry_run_returns_legs(self):
        result = execute_options_trade(
            trader_id="alex",
            ticker="AAPL",
            strategy="long_call",
            strikes={"strike": 150.0, "expiration_days": 30},
            greeks={"price": 3.50, "delta": 0.45, "theta": -0.04,
                    "vega": 0.12, "gamma": 0.02},
            risk_assessment={"contracts": 1, "max_loss_total": 350.0},
            dry_run=True,
        )
        assert isinstance(result["legs"], list)
        assert len(result["legs"]) > 0

    def test_dry_run_iron_condor_has_four_legs(self):
        result = execute_options_trade(
            trader_id="casey",
            ticker="SPY",
            strategy="iron_condor",
            strikes={
                "put_long_strike": 400.0,
                "put_short_strike": 405.0,
                "call_short_strike": 415.0,
                "call_long_strike": 420.0,
                "expiration_days": 30,
            },
            greeks={"price": 1.80, "delta": 0.02, "theta": 0.05,
                    "vega": -0.10, "gamma": -0.01},
            risk_assessment={"contracts": 1, "max_loss_total": 320.0},
            dry_run=True,
        )
        assert result["success"] is True
        assert len(result["legs"]) == 4

    def test_dry_run_bull_put_spread_has_two_legs(self):
        result = execute_options_trade(
            trader_id="alex",
            ticker="AAPL",
            strategy="bull_put_spread",
            strikes={
                "short_strike": 145.0,
                "long_strike": 140.0,
                "expiration_days": 30,
            },
            greeks={"price": 1.50, "delta": 0.25, "theta": 0.03,
                    "vega": -0.08, "gamma": -0.01},
            risk_assessment={"contracts": 2, "max_loss_total": 700.0},
            dry_run=True,
        )
        assert result["success"] is True
        assert len(result["legs"]) == 2

    def test_zero_contracts_returns_failure(self):
        result = execute_options_trade(
            trader_id="alex",
            ticker="AAPL",
            strategy="long_call",
            strikes={"strike": 150.0, "expiration_days": 30},
            greeks={"price": 3.50, "delta": 0.45, "theta": -0.04,
                    "vega": 0.12, "gamma": 0.02},
            risk_assessment={"contracts": 0, "max_loss_total": 0.0},
            dry_run=True,
        )
        assert result["success"] is False
        assert "Zero contracts" in result["error"]

    def test_unknown_strategy_returns_failure(self):
        result = execute_options_trade(
            trader_id="alex",
            ticker="AAPL",
            strategy="butterfly_spread",
            strikes={"strike": 150.0, "expiration_days": 30},
            greeks={"price": 1.0},
            risk_assessment={"contracts": 1, "max_loss_total": 100.0},
            dry_run=True,
        )
        assert result["success"] is False


class TestRunPositionMonitor:

    def test_empty_positions_returns_zero_checked(self):
        with patch("agents.options.position_monitor._get_open_positions",
                   return_value=[]):
            result = run_position_monitor()
        assert result["checked"] == 0
        assert result["closed"] == 0

    def test_expired_position_is_closed(self):
        mock_position = {
            "id": "test-uuid-1",
            "ticker": "AAPL",
            "strategy": "long_call",
            "status": "open",
            "contracts": 1,
            "legs": [{"symbol": "AAPL251219C00150000", "side": "buy",
                      "type": "call", "strike": 150.0}],
            "max_loss_total": 350.0,
            "net_credit_debit": -3.50,
            "entry_price": 3.50,
            "expiration_date": "2020-01-01",  # expired
        }
        with patch("agents.options.position_monitor._get_open_positions",
                   return_value=[mock_position]), \
             patch("agents.options.position_monitor._get_current_option_price",
                   return_value=0.01), \
             patch("agents.options.position_monitor._close_position") as mock_close:
            result = run_position_monitor()

        assert result["closed"] == 1
        assert "expired" in result["close_reasons"]
        mock_close.assert_called_once()

    def test_healthy_position_not_closed(self):
        future_date = (date.today() + timedelta(days=20)).isoformat()
        mock_position = {
            "id": "test-uuid-2",
            "ticker": "MSFT",
            "strategy": "bull_put_spread",
            "status": "open",
            "contracts": 1,
            "legs": [],
            "max_loss_total": 400.0,
            "net_credit_debit": 1.50,
            "entry_price": -1.50,
            "expiration_date": future_date,
        }
        with patch("agents.options.position_monitor._get_open_positions",
                   return_value=[mock_position]), \
             patch("agents.options.position_monitor._get_current_option_price",
                   return_value=-1.20), \
             patch("agents.options.position_monitor._update_unrealized_pnl") as mock_update:
            result = run_position_monitor()

        assert result["closed"] == 0
        mock_update.assert_called_once()

    def test_returns_summary_dict_shape(self):
        with patch("agents.options.position_monitor._get_open_positions",
                   return_value=[]):
            result = run_position_monitor()
        assert set(result.keys()) == {"checked", "closed", "close_reasons", "errors"}
