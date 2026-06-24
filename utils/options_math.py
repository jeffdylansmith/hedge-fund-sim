"""
options_math.py

Black-Scholes pricing and Greeks for European options.
All calculations are pure Python/numpy — no LLM, no API calls.

Inputs use these conventions throughout:
  S  = current stock price (float)
  K  = strike price (float)
  T  = time to expiration in years (float, e.g. 30 days = 30/365)
  r  = risk-free rate as decimal (float, e.g. 5% = 0.05)
  sigma = implied volatility as decimal (float, e.g. 30% = 0.30)
  option_type = "call" or "put"
"""

import math
from scipy.stats import norm


def _d1(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    d1 component of Black-Scholes.
    Measures how favorable the current stock price is relative to the strike,
    adjusted for time and volatility.
    Returns infinity if T <= 0 (expired option).
    """
    if T <= 0:
        return float("inf")
    return (math.log(S / K) + (r + 0.5 * sigma ** 2) * T) / (sigma * math.sqrt(T))


def _d2(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    d2 component of Black-Scholes.
    d1 shifted by volatility over time.
    Approximates probability option expires in-the-money.
    """
    if T <= 0:
        return float("inf")
    return _d1(S, K, T, r, sigma) - sigma * math.sqrt(T)


def black_scholes_price(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> float:
    """
    Black-Scholes fair value for a European option.

    Returns 0.0 if T <= 0 (expired, worthless unless deep ITM —
    for simplicity we return intrinsic value at expiration instead).
    """
    if T <= 0:
        if option_type == "call":
            return max(0.0, S - K)
        else:
            return max(0.0, K - S)

    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)

    if option_type == "call":
        return S * norm.cdf(d1) - K * math.exp(-r * T) * norm.cdf(d2)
    elif option_type == "put":
        return K * math.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1)
    else:
        raise ValueError(f"option_type must be 'call' or 'put', got: {option_type}")


def delta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """
    Delta: rate of change of option price with respect to stock price.
    Call delta: 0 to 1. Put delta: -1 to 0.
    At-the-money options have delta ~0.50 (call) or ~-0.50 (put).
    """
    if T <= 0:
        return 1.0 if (option_type == "call" and S > K) else 0.0
    d1 = _d1(S, K, T, r, sigma)
    if option_type == "call":
        return norm.cdf(d1)
    else:
        return norm.cdf(d1) - 1.0


def gamma(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Gamma: rate of change of delta with respect to stock price.
    Same value for calls and puts (second derivative of price).
    High gamma = delta shifting rapidly = position accelerating.
    Peaks at-the-money near expiration.
    """
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return norm.pdf(d1) / (S * sigma * math.sqrt(T))


def theta(S: float, K: float, T: float, r: float, sigma: float, option_type: str = "call") -> float:
    """
    Theta: rate of change of option price with respect to time.
    Returned as value lost PER DAY (divided by 365).
    Always negative for long options — time works against buyers.
    """
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    d2 = _d2(S, K, T, r, sigma)

    term1 = -(S * norm.pdf(d1) * sigma) / (2 * math.sqrt(T))

    if option_type == "call":
        term2 = -r * K * math.exp(-r * T) * norm.cdf(d2)
    else:
        term2 = r * K * math.exp(-r * T) * norm.cdf(-d2)

    return (term1 + term2) / 365  # per-day theta


def vega(S: float, K: float, T: float, r: float, sigma: float) -> float:
    """
    Vega: rate of change of option price with respect to implied volatility.
    Returned per 1% move in IV (divided by 100).
    Same value for calls and puts.
    High vega = position sensitive to volatility changes.
    """
    if T <= 0:
        return 0.0
    d1 = _d1(S, K, T, r, sigma)
    return S * norm.pdf(d1) * math.sqrt(T) / 100  # per 1% vol move


def implied_volatility(
    market_price: float,
    S: float,
    K: float,
    T: float,
    r: float,
    option_type: str = "call",
    max_iterations: int = 100,
    precision: float = 1e-5,
) -> float:
    """
    Implied Volatility via Newton-Raphson iteration.

    Black-Scholes has no closed-form solution for sigma given price,
    so we iterate: guess sigma, compute BS price, compare to market price,
    adjust sigma using vega as the gradient, repeat until converged.

    Returns -1.0 if no solution found within max_iterations.
    """
    sigma = 0.30  # initial guess: 30% IV

    for _ in range(max_iterations):
        price = black_scholes_price(S, K, T, r, sigma, option_type)
        v = vega(S, K, T, r, sigma) * 100  # undo the /100 for Newton step

        if abs(v) < 1e-10:
            return -1.0  # vega too small, can't converge

        sigma_new = sigma - (price - market_price) / v

        if abs(sigma_new - sigma) < precision:
            return max(0.001, sigma_new)  # IV can't be negative

        sigma = max(0.001, sigma_new)

    return -1.0  # did not converge


def expected_move(S: float, sigma: float, days: int) -> float:
    """
    Expected one-standard-deviation move over N days.
    Formula: S × sigma × sqrt(days/365)

    Interpretation: stock stays within ±expected_move with ~68% probability.
    Used for strike selection in iron condors and defined-risk spreads.
    """
    return S * sigma * math.sqrt(days / 365)


def all_greeks(
    S: float,
    K: float,
    T: float,
    r: float,
    sigma: float,
    option_type: str = "call",
) -> dict:
    """
    Returns all Greeks and price in a single dict.
    Convenience function for agent consumption.
    """
    return {
        "price": black_scholes_price(S, K, T, r, sigma, option_type),
        "delta": delta(S, K, T, r, sigma, option_type),
        "gamma": gamma(S, K, T, r, sigma),
        "theta": theta(S, K, T, r, sigma, option_type),
        "vega": vega(S, K, T, r, sigma),
        "option_type": option_type,
        "S": S, "K": K, "T": T, "r": r, "sigma": sigma,
    }
