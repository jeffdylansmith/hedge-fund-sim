"""
Tests for utils/options_math.py

These tests verify mathematical properties of the Greeks and pricing,
not just that functions run — they check that the math is correct.
"""

import math
import pytest
from utils.options_math import (
    black_scholes_price,
    delta,
    gamma,
    theta,
    vega,
    implied_volatility,
    expected_move,
    all_greeks,
)

# Standard test inputs — roughly AAPL-like scenario
S = 150.0    # stock price
K = 150.0    # at-the-money strike
T = 30/365   # 30 days to expiration
r = 0.05     # 5% risk-free rate
sigma = 0.25 # 25% IV


class TestBlackScholesPrice:

    def test_call_price_positive(self):
        price = black_scholes_price(S, K, T, r, sigma, "call")
        assert price > 0

    def test_put_price_positive(self):
        price = black_scholes_price(S, K, T, r, sigma, "put")
        assert price > 0

    def test_put_call_parity(self):
        """
        Put-call parity: Call - Put = S - K*e^(-rT)
        This is a fundamental no-arbitrage relationship.
        If this fails, the pricing model is broken.
        """
        call = black_scholes_price(S, K, T, r, sigma, "call")
        put = black_scholes_price(S, K, T, r, sigma, "put")
        parity = S - K * math.exp(-r * T)
        assert abs((call - put) - parity) < 0.01

    def test_deep_itm_call_approaches_intrinsic(self):
        """Deep in-the-money call should approach S - K."""
        price = black_scholes_price(200.0, 100.0, T, r, sigma, "call")
        intrinsic = 200.0 - 100.0
        assert price > intrinsic * 0.95  # within 5% of intrinsic

    def test_far_otm_call_near_zero(self):
        """Far out-of-the-money call with little time should be nearly worthless."""
        price = black_scholes_price(100.0, 200.0, 5/365, r, sigma, "call")
        assert price < 0.01

    def test_expired_option_returns_intrinsic(self):
        call = black_scholes_price(160.0, 150.0, 0, r, sigma, "call")
        assert abs(call - 10.0) < 0.001
        put = black_scholes_price(140.0, 150.0, 0, r, sigma, "put")
        assert abs(put - 10.0) < 0.001

    def test_invalid_option_type_raises(self):
        with pytest.raises(ValueError):
            black_scholes_price(S, K, T, r, sigma, "banana")


class TestDelta:

    def test_call_delta_between_0_and_1(self):
        d = delta(S, K, T, r, sigma, "call")
        assert 0.0 <= d <= 1.0

    def test_put_delta_between_minus1_and_0(self):
        d = delta(S, K, T, r, sigma, "put")
        assert -1.0 <= d <= 0.0

    def test_atm_call_delta_near_half(self):
        """ATM call delta should be close to 0.50."""
        d = delta(S, K, T, r, sigma, "call")
        assert 0.45 <= d <= 0.55

    def test_deep_itm_call_delta_near_one(self):
        d = delta(200.0, 100.0, T, r, sigma, "call")
        assert d > 0.95

    def test_call_and_put_delta_sum_to_one(self):
        """Call delta + abs(put delta) = 1. Another no-arbitrage identity."""
        call_d = delta(S, K, T, r, sigma, "call")
        put_d = delta(S, K, T, r, sigma, "put")
        assert abs(call_d + abs(put_d) - 1.0) < 0.001  # note: put delta is negative


class TestGamma:

    def test_gamma_positive(self):
        g = gamma(S, K, T, r, sigma)
        assert g > 0

    def test_gamma_higher_atm_than_otm(self):
        """Gamma peaks at-the-money."""
        atm_gamma = gamma(150.0, 150.0, T, r, sigma)
        otm_gamma = gamma(150.0, 200.0, T, r, sigma)
        assert atm_gamma > otm_gamma


class TestTheta:

    def test_theta_negative_for_long_call(self):
        """Buyers lose value daily — theta must be negative."""
        t = theta(S, K, T, r, sigma, "call")
        assert t < 0

    def test_theta_negative_for_long_put(self):
        t = theta(S, K, T, r, sigma, "put")
        assert t < 0

    def test_theta_faster_near_expiration(self):
        """Time decay accelerates as expiration approaches."""
        far_theta = theta(S, K, 90/365, r, sigma, "call")
        near_theta = theta(S, K, 5/365, r, sigma, "call")
        assert abs(near_theta) > abs(far_theta)


class TestVega:

    def test_vega_positive(self):
        v = vega(S, K, T, r, sigma)
        assert v > 0

    def test_vega_per_one_percent(self):
        """Vega should be per 1% IV move — roughly $0.05-$0.15 for a 30-day ATM option."""
        v = vega(S, K, T, r, sigma)
        assert 0.01 < v < 1.0  # sanity range


class TestImpliedVolatility:

    def test_iv_roundtrip(self):
        """
        Compute a BS price, then back out IV from that price.
        Should recover original sigma within tolerance.
        """
        price = black_scholes_price(S, K, T, r, sigma, "call")
        recovered_iv = implied_volatility(price, S, K, T, r, "call")
        assert abs(recovered_iv - sigma) < 0.001

    def test_iv_roundtrip_put(self):
        price = black_scholes_price(S, K, T, r, sigma, "put")
        recovered_iv = implied_volatility(price, S, K, T, r, "put")
        assert abs(recovered_iv - sigma) < 0.001


class TestExpectedMove:

    def test_expected_move_positive(self):
        em = expected_move(150.0, 0.25, 30)
        assert em > 0

    def test_expected_move_formula(self):
        """Verify formula: S × sigma × sqrt(days/365)"""
        em = expected_move(200.0, 0.30, 30)
        expected = 200.0 * 0.30 * math.sqrt(30/365)
        assert abs(em - expected) < 0.0001

    def test_longer_duration_larger_move(self):
        short = expected_move(150.0, 0.25, 7)
        long_ = expected_move(150.0, 0.25, 60)
        assert long_ > short


class TestAllGreeks:

    def test_returns_all_keys(self):
        result = all_greeks(S, K, T, r, sigma, "call")
        expected_keys = {"price", "delta", "gamma", "theta", "vega",
                        "option_type", "S", "K", "T", "r", "sigma"}
        assert expected_keys == result.keys()
