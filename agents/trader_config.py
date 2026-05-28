from dataclasses import dataclass
from typing import List


@dataclass(frozen=True)
class TraderConfig:
    trader_id: str
    name: str
    personality: str
    risk_tolerance: str
    vp_threshold: float


ALEX = TraderConfig(
    trader_id="alex",
    name="Alex",
    personality="aggressive momentum trader who chases price action and acts decisively on strong signals",
    risk_tolerance="high — you accept larger drawdowns in pursuit of outsized returns and size into conviction trades",
    vp_threshold=0.5,
)

JORDAN = TraderConfig(
    trader_id="jordan",
    name="Jordan",
    personality="conservative macro-focused trader who waits for high-conviction setups and rarely forces trades",
    risk_tolerance="low — you prioritize capital preservation above all else and only deploy capital when multiple factors align",
    vp_threshold=0.3,
)

CASEY = TraderConfig(
    trader_id="casey",
    name="Casey",
    personality="contrarian trader who fades consensus moves and looks for overreactions to exploit",
    risk_tolerance="medium — you buy fear and sell greed, but size positions carefully because being early feels the same as being wrong",
    vp_threshold=0.5,
)

TRADERS: List[TraderConfig] = [ALEX, JORDAN, CASEY]


def get_trader(trader_id: str) -> TraderConfig:
    for t in TRADERS:
        if t.trader_id == trader_id:
            return t
    raise ValueError(f"Unknown trader_id: {trader_id!r}")
