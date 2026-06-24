import { useState, useEffect, useCallback } from "react";
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid,
  Tooltip, ReferenceLine, ResponsiveContainer, Area, AreaChart
} from "recharts";

const API = import.meta.env.VITE_API_URL || "http://localhost:8000";
const TRADERS = ["alex", "jordan", "casey"];

// ─── Helpers ────────────────────────────────────────────────────────────────

function fmt$(n) {
  if (n == null) return "—";
  return new Intl.NumberFormat("en-US", {
    style: "currency", currency: "USD", maximumFractionDigits: 0
  }).format(n);
}

function fmtPct(n) {
  if (n == null) return "—";
  return `${n.toFixed(1)}%`;
}

function fmtGreek(n, decimals = 3) {
  if (n == null) return "—";
  return n.toFixed(decimals);
}

// Black-Scholes helpers for payoff diagram (client-side)
// These mirror utils/options_math.py so the frontend can
// render payoff diagrams without an extra API round-trip.

function normalCDF(x) {
  // Abramowitz and Stegun approximation — accurate to 7 decimal places
  const t = 1 / (1 + 0.2316419 * Math.abs(x));
  const d = 0.3989422820 * Math.exp(-0.5 * x * x);
  const p = d * t * (0.3193815 + t * (-0.3565638 + t * (1.7814779
    + t * (-1.8212560 + t * 1.3302744))));
  return x >= 0 ? 1 - p : p;
}

function bsPrice(S, K, T, r, sigma, type) {
  if (T <= 0) return type === "call" ? Math.max(0, S - K) : Math.max(0, K - S);
  const d1 = (Math.log(S / K) + (r + 0.5 * sigma ** 2) * T)
    / (sigma * Math.sqrt(T));
  const d2 = d1 - sigma * Math.sqrt(T);
  if (type === "call") {
    return S * normalCDF(d1) - K * Math.exp(-r * T) * normalCDF(d2);
  }
  return K * Math.exp(-r * T) * normalCDF(-d2) - S * normalCDF(-d1);
}

function computePayoff(strategy, strikes, currentPrice, iv = 0.25) {
  // Generate P&L curve across price range ±30% from current
  const low = currentPrice * 0.70;
  const high = currentPrice * 1.30;
  const steps = 80;
  const r = 0.05;
  const T_entry = 30 / 365;
  const T_half = 15 / 365;
  const points = [];

  for (let i = 0; i <= steps; i++) {
    const S = low + (high - low) * (i / steps);

    let atExpiry = 0;
    let today = 0;

    if (strategy === "long_call") {
      const K = strikes.strike || currentPrice;
      const premium = bsPrice(currentPrice, K, T_entry, r, iv, "call");
      atExpiry = Math.max(0, S - K) - premium;
      today = bsPrice(S, K, T_half, r, iv, "call") - premium;
    } else if (strategy === "long_put") {
      const K = strikes.strike || currentPrice;
      const premium = bsPrice(currentPrice, K, T_entry, r, iv, "put");
      atExpiry = Math.max(0, K - S) - premium;
      today = bsPrice(S, K, T_half, r, iv, "put") - premium;
    } else if (strategy === "bull_put_spread") {
      const Ks = strikes.short_strike || currentPrice * 0.97;
      const Kl = strikes.long_strike || currentPrice * 0.93;
      const credit = bsPrice(currentPrice, Ks, T_entry, r, iv, "put")
        - bsPrice(currentPrice, Kl, T_entry, r, iv, "put");
      atExpiry = Math.max(0, Ks - S) - Math.max(0, Kl - S) + credit;
      today = (bsPrice(S, Ks, T_half, r, iv, "put")
        - bsPrice(S, Kl, T_half, r, iv, "put")) - (-credit);
    } else if (strategy === "bear_call_spread") {
      const Ks = strikes.short_strike || currentPrice * 1.03;
      const Kl = strikes.long_strike || currentPrice * 1.07;
      const credit = bsPrice(currentPrice, Ks, T_entry, r, iv, "call")
        - bsPrice(currentPrice, Kl, T_entry, r, iv, "call");
      atExpiry = Math.max(0, S - Ks) - Math.max(0, S - Kl) + credit;
      today = (bsPrice(S, Ks, T_half, r, iv, "call")
        - bsPrice(S, Kl, T_half, r, iv, "call")) - (-credit);
    } else if (strategy === "iron_condor") {
      const Kps = strikes.put_short_strike || currentPrice * 0.95;
      const Kpl = strikes.put_long_strike || currentPrice * 0.91;
      const Kcs = strikes.call_short_strike || currentPrice * 1.05;
      const Kcl = strikes.call_long_strike || currentPrice * 1.09;
      const credit =
        (bsPrice(currentPrice, Kps, T_entry, r, iv, "put")
          - bsPrice(currentPrice, Kpl, T_entry, r, iv, "put")) +
        (bsPrice(currentPrice, Kcs, T_entry, r, iv, "call")
          - bsPrice(currentPrice, Kcl, T_entry, r, iv, "call"));
      const putPnl = Math.max(0, Kps - S) - Math.max(0, Kpl - S);
      const callPnl = Math.max(0, S - Kcs) - Math.max(0, S - Kcl);
      atExpiry = credit - putPnl - callPnl;
      today = credit * 0.5 * (1 - Math.abs(S - currentPrice)
        / (currentPrice * 0.10));
    }

    // Per-contract dollar values (×100 shares per contract)
    points.push({
      price: +S.toFixed(2),
      atExpiry: +(atExpiry * 100).toFixed(2),
      today: +(today * 100).toFixed(2),
    });
  }
  return points;
}

// ─── Sub-components ──────────────────────────────────────────────────────────

function ExposureCard({ trader }) {
  const [data, setData] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    fetch(`${API}/options/exposure/${trader}`)
      .then(r => r.json())
      .then(setData)
      .catch(() => setData(null))
      .finally(() => setLoading(false));
  }, [trader]);

  if (loading) return (
    <div className="bg-gray-800 rounded-lg p-4 animate-pulse h-32" />
  );
  if (!data) return null;

  const utilPct = data.utilization_pct || 0;
  const barColor = utilPct > 80 ? "#ef4444"
    : utilPct > 50 ? "#f59e0b" : "#10b981";

  return (
    <div className="bg-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-start mb-3">
        <div>
          <h3 className="text-white font-semibold capitalize">{trader}</h3>
          <p className="text-gray-400 text-xs mt-0.5">
            Cap: {fmt$(data.options_risk_cap_dollars)}
          </p>
        </div>
        <span className="text-xs px-2 py-1 rounded bg-gray-700 text-gray-300">
          {data.open_positions} open
        </span>
      </div>

      {/* Utilization bar */}
      <div className="mb-2">
        <div className="flex justify-between text-xs text-gray-400 mb-1">
          <span>Risk utilized</span>
          <span style={{ color: barColor }}>{fmtPct(utilPct)}</span>
        </div>
        <div className="w-full bg-gray-700 rounded-full h-2">
          <div
            className="h-2 rounded-full transition-all duration-500"
            style={{
              width: `${Math.min(utilPct, 100)}%`,
              backgroundColor: barColor
            }}
          />
        </div>
      </div>

      <div className="flex justify-between text-xs mt-3">
        <div>
          <p className="text-gray-400">Exposure</p>
          <p className="text-white font-medium">
            {fmt$(data.current_exposure)}
          </p>
        </div>
        <div className="text-right">
          <p className="text-gray-400">Remaining</p>
          <p className="text-green-400 font-medium">
            {fmt$(data.remaining_capacity)}
          </p>
        </div>
      </div>
    </div>
  );
}


function GreeksPanel({ greeks, strategy }) {
  if (!greeks || Object.keys(greeks).length === 0) return null;

  const items = [
    {
      label: "Delta", value: greeks.delta,
      desc: "P&L per $1 stock move",
      color: greeks.delta > 0 ? "text-green-400" : "text-red-400"
    },
    {
      label: "Gamma", value: greeks.gamma,
      desc: "Delta acceleration",
      color: "text-blue-400"
    },
    {
      label: "Theta", value: greeks.theta,
      desc: "Daily time decay (per contract)",
      color: greeks.theta > 0 ? "text-green-400" : "text-red-400"
    },
    {
      label: "Vega", value: greeks.vega,
      desc: "P&L per 1% IV move",
      color: greeks.vega > 0 ? "text-purple-400" : "text-orange-400"
    },
  ];

  return (
    <div className="grid grid-cols-2 gap-3 mt-3">
      {items.map(({ label, value, desc, color }) => (
        <div key={label} className="bg-gray-700 rounded p-3">
          <div className="flex justify-between items-baseline">
            <span className="text-gray-400 text-xs">{label}</span>
            <span className={`font-mono font-semibold text-sm ${color}`}>
              {fmtGreek(value)}
            </span>
          </div>
          <p className="text-gray-500 text-xs mt-1">{desc}</p>
        </div>
      ))}
    </div>
  );
}


function PayoffDiagram({ strategy, strikes, currentPrice, iv }) {
  if (!strategy || strategy === "skip" || !currentPrice) return null;

  const data = computePayoff(strategy, strikes || {}, currentPrice, iv || 0.25);
  const maxVal = Math.max(...data.map(d => Math.max(
    Math.abs(d.atExpiry), Math.abs(d.today)
  )));
  const yDomain = [-maxVal * 1.2, maxVal * 1.2];

  return (
    <div className="mt-4">
      <h4 className="text-gray-300 text-sm font-medium mb-3">
        Payoff Diagram — {strategy.replace(/_/g, " ")}
      </h4>
      <p className="text-gray-500 text-xs mb-3">
        P&L per contract (100 shares) at expiry and at ~15 days remaining
      </p>
      <ResponsiveContainer width="100%" height={240}>
        <LineChart data={data}
          margin={{ top: 5, right: 20, left: 10, bottom: 5 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#374151" />
          <XAxis
            dataKey="price"
            tickFormatter={v => `$${v}`}
            stroke="#6b7280"
            tick={{ fontSize: 11 }}
            interval={Math.floor(data.length / 6)}
          />
          <YAxis
            domain={yDomain}
            tickFormatter={v => `$${v}`}
            stroke="#6b7280"
            tick={{ fontSize: 11 }}
            width={55}
          />
          <Tooltip
            formatter={(v, name) => [
              `$${v.toFixed(2)}`,
              name === "atExpiry" ? "At Expiry" : "Today (~15 DTE)"
            ]}
            labelFormatter={v => `Stock: $${v}`}
            contentStyle={{
              backgroundColor: "#1f2937",
              border: "1px solid #374151",
              borderRadius: "6px",
              color: "#f9fafb"
            }}
          />
          <ReferenceLine y={0} stroke="#6b7280" strokeWidth={1} />
          <ReferenceLine
            x={currentPrice}
            stroke="#60a5fa"
            strokeDasharray="4 4"
            label={{
              value: "Current",
              position: "top",
              fill: "#60a5fa",
              fontSize: 11
            }}
          />
          <Line
            type="monotone"
            dataKey="atExpiry"
            stroke="#10b981"
            strokeWidth={2}
            dot={false}
            name="atExpiry"
          />
          <Line
            type="monotone"
            dataKey="today"
            stroke="#f59e0b"
            strokeWidth={1.5}
            strokeDasharray="5 5"
            dot={false}
            name="today"
          />
        </LineChart>
      </ResponsiveContainer>
      <div className="flex gap-4 mt-2 justify-center">
        <div className="flex items-center gap-1.5">
          <div className="w-6 h-0.5 bg-green-400" />
          <span className="text-gray-400 text-xs">At expiry</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-6 h-0.5 bg-yellow-400 border-dashed" />
          <span className="text-gray-400 text-xs">Today (~15 DTE)</span>
        </div>
        <div className="flex items-center gap-1.5">
          <div className="w-0.5 h-3 bg-blue-400" />
          <span className="text-gray-400 text-xs">Current price</span>
        </div>
      </div>
    </div>
  );
}


// ─── Main Page ───────────────────────────────────────────────────────────────

export default function Options() {
  // Analyzer state
  const [ticker, setTicker] = useState("AAPL");
  const [selectedTrader, setSelectedTrader] = useState("alex");
  const [analyzing, setAnalyzing] = useState(false);
  const [analysisResult, setAnalysisResult] = useState(null);
  const [analysisError, setAnalysisError] = useState(null);

  // Positions state
  const [positions, setPositions] = useState([]);
  const [positionsLoading, setPositionsLoading] = useState(true);

  // Selected position for payoff diagram
  const [selectedPosition, setSelectedPosition] = useState(null);

  // Load open positions
  const loadPositions = useCallback(() => {
    setPositionsLoading(true);
    fetch(`${API}/options/positions`)
      .then(r => r.json())
      .then(d => setPositions(d.positions || []))
      .catch(() => setPositions([]))
      .finally(() => setPositionsLoading(false));
  }, []);

  useEffect(() => { loadPositions(); }, [loadPositions]);

  // Run options analysis
  const runAnalysis = async () => {
    if (!ticker.trim()) return;
    setAnalyzing(true);
    setAnalysisResult(null);
    setAnalysisError(null);

    try {
      const res = await fetch(`${API}/options/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          ticker: ticker.toUpperCase().trim(),
          trader_id: selectedTrader,
        }),
      });
      const data = await res.json();
      setAnalysisResult(data);
    } catch (e) {
      setAnalysisError("Analysis failed — check API connection.");
    } finally {
      setAnalyzing(false);
    }
  };

  const decisionColor = (d) => {
    if (d === "APPROVE") return "text-green-400";
    if (d === "REJECT") return "text-red-400";
    return "text-gray-400";
  };

  const ivEnvColor = (env) => {
    if (env === "high") return "text-red-400";
    if (env === "low") return "text-green-400";
    return "text-yellow-400";
  };

  return (
    <div className="min-h-screen bg-gray-900 text-white p-6">
      <div className="max-w-6xl mx-auto">

        {/* Header */}
        <div className="mb-8">
          <h1 className="text-2xl font-bold text-white">Options Dashboard</h1>
          <p className="text-gray-400 mt-1 text-sm">
            Multi-agent options analysis — volatility, thesis, strategy,
            and risk gate
          </p>
        </div>

        {/* Exposure Summary */}
        <section className="mb-8">
          <h2 className="text-lg font-semibold text-gray-200 mb-4">
            Options Risk Exposure
          </h2>
          <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
            {TRADERS.map(t => <ExposureCard key={t} trader={t} />)}
          </div>
        </section>

        {/* Analyzer */}
        <section className="mb-8">
          <h2 className="text-lg font-semibold text-gray-200 mb-4">
            Options Analyzer
          </h2>
          <div className="bg-gray-800 rounded-lg p-6">

            {/* Controls */}
            <div className="flex gap-3 mb-6 flex-wrap">
              <input
                type="text"
                value={ticker}
                onChange={e => setTicker(e.target.value.toUpperCase())}
                onKeyDown={e => e.key === "Enter" && runAnalysis()}
                placeholder="Ticker (e.g. AAPL)"
                className="bg-gray-700 text-white px-4 py-2 rounded-lg
                  border border-gray-600 focus:outline-none
                  focus:border-blue-500 w-40 font-mono"
              />
              <select
                value={selectedTrader}
                onChange={e => setSelectedTrader(e.target.value)}
                className="bg-gray-700 text-white px-4 py-2 rounded-lg
                  border border-gray-600 focus:outline-none
                  focus:border-blue-500 capitalize"
              >
                {TRADERS.map(t => (
                  <option key={t} value={t}>{t}</option>
                ))}
              </select>
              <button
                onClick={runAnalysis}
                disabled={analyzing}
                className="bg-blue-600 hover:bg-blue-700 disabled:bg-gray-600
                  text-white px-6 py-2 rounded-lg font-medium
                  transition-colors duration-150"
              >
                {analyzing ? "Analyzing…" : "Analyze"}
              </button>
            </div>

            {/* Result */}
            {analysisError && (
              <div className="text-red-400 text-sm bg-red-900/20
                rounded-lg p-3 mb-4">
                {analysisError}
              </div>
            )}

            {analysisResult && (
              <div>
                {/* Decision header */}
                <div className="flex items-center gap-4 mb-4 pb-4
                  border-b border-gray-700">
                  <div>
                    <p className="text-gray-400 text-xs mb-1">Decision</p>
                    <p className={`text-2xl font-bold
                      ${decisionColor(analysisResult.decision)}`}>
                      {analysisResult.decision}
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-xs mb-1">Strategy</p>
                    <p className="text-white font-semibold capitalize">
                      {analysisResult.strategy?.replace(/_/g, " ") || "—"}
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-xs mb-1">Contracts</p>
                    <p className="text-white font-semibold">
                      {analysisResult.contracts}
                    </p>
                  </div>
                  <div>
                    <p className="text-gray-400 text-xs mb-1">Max Loss</p>
                    <p className="text-red-400 font-semibold">
                      {fmt$(analysisResult.max_loss_total)}
                    </p>
                  </div>
                </div>

                {/* Volatility + Thesis */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
                  {analysisResult.volatility_summary && (
                    <div className="bg-gray-700 rounded-lg p-4">
                      <h4 className="text-gray-300 text-sm font-medium mb-3">
                        Volatility Assessment
                      </h4>
                      <div className="space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-400">IV Environment</span>
                          <span className={`font-medium capitalize
                            ${ivEnvColor(
                              analysisResult.volatility_summary.iv_environment
                            )}`}>
                            {analysisResult.volatility_summary.iv_environment}
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-400">IV Rank</span>
                          <span className="text-white font-medium">
                            {fmtPct(
                              analysisResult.volatility_summary.iv_rank
                            )}
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-400">Expected Move 30d</span>
                          <span className="text-white font-medium">
                            {fmt$(
                              analysisResult.volatility_summary.expected_move_30d
                            )}
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-400">Recommendation</span>
                          <span className="text-blue-400 font-medium capitalize">
                            {analysisResult.volatility_summary.recommendation
                              ?.replace(/_/g, " ")}
                          </span>
                        </div>
                      </div>
                    </div>
                  )}

                  {analysisResult.thesis_summary && (
                    <div className="bg-gray-700 rounded-lg p-4">
                      <h4 className="text-gray-300 text-sm font-medium mb-3">
                        Directional Thesis
                      </h4>
                      <div className="space-y-2">
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-400">Direction</span>
                          <span className={`font-medium capitalize
                            ${analysisResult.thesis_summary.direction
                              === "bullish" ? "text-green-400"
                              : analysisResult.thesis_summary.direction
                                === "bearish" ? "text-red-400"
                              : "text-gray-300"}`}>
                            {analysisResult.thesis_summary.direction}
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-400">Conviction</span>
                          <span className="text-white font-medium">
                            {analysisResult.thesis_summary.conviction != null
                              ? fmtPct(
                                  analysisResult.thesis_summary.conviction * 100
                                )
                              : "—"}
                          </span>
                        </div>
                        <div className="flex justify-between text-sm">
                          <span className="text-gray-400">Catalyst</span>
                          <span className="text-gray-300 text-right max-w-[60%]">
                            {analysisResult.thesis_summary.catalyst || "—"}
                          </span>
                        </div>
                      </div>
                    </div>
                  )}
                </div>

                {/* Explanation */}
                <div className="bg-gray-700/50 rounded-lg p-3 mb-4">
                  <p className="text-gray-300 text-sm italic">
                    "{analysisResult.explanation}"
                  </p>
                </div>

                {/* Payoff diagram for approved trades */}
                {analysisResult.decision === "APPROVE" && (
                  <PayoffDiagram
                    strategy={analysisResult.strategy}
                    strikes={{}}
                    currentPrice={150}
                    iv={analysisResult.volatility_summary?.iv_rank
                      ? analysisResult.volatility_summary.iv_rank / 100 * 0.5
                      : 0.25}
                  />
                )}

                {/* Errors */}
                {analysisResult.errors?.length > 0 && (
                  <div className="mt-3 text-xs text-gray-500">
                    Pipeline notes: {analysisResult.errors.join("; ")}
                  </div>
                )}
              </div>
            )}
          </div>
        </section>

        {/* Open Positions */}
        <section className="mb-8">
          <div className="flex justify-between items-center mb-4">
            <h2 className="text-lg font-semibold text-gray-200">
              Open Positions
            </h2>
            <button
              onClick={loadPositions}
              className="text-gray-400 hover:text-white text-sm
                transition-colors"
            >
              Refresh
            </button>
          </div>

          {positionsLoading ? (
            <div className="bg-gray-800 rounded-lg p-8 text-center
              text-gray-500">
              Loading positions…
            </div>
          ) : positions.length === 0 ? (
            <div className="bg-gray-800 rounded-lg p-8 text-center">
              <p className="text-gray-400">No open options positions.</p>
              <p className="text-gray-600 text-sm mt-1">
                Run an analysis and approve a trade to see positions here.
              </p>
            </div>
          ) : (
            <div className="bg-gray-800 rounded-lg overflow-hidden">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-gray-700">
                    {["Trader", "Ticker", "Strategy", "Contracts",
                      "Max Loss", "Unrealized P&L",
                      "Expiry", ""].map(h => (
                      <th key={h}
                        className="text-left text-gray-400 text-xs
                          font-medium px-4 py-3">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {positions.map(pos => (
                    <tr key={pos.id}
                      className="border-b border-gray-700/50
                        hover:bg-gray-700/30 transition-colors">
                      <td className="px-4 py-3 text-gray-300 capitalize
                        text-sm">
                        {pos.trader_id}
                      </td>
                      <td className="px-4 py-3 text-white font-mono
                        font-semibold text-sm">
                        {pos.ticker}
                      </td>
                      <td className="px-4 py-3 text-gray-300 text-sm
                        capitalize">
                        {pos.strategy?.replace(/_/g, " ")}
                      </td>
                      <td className="px-4 py-3 text-gray-300 text-sm">
                        {pos.contracts}
                      </td>
                      <td className="px-4 py-3 text-red-400 text-sm
                        font-medium">
                        {fmt$(pos.max_loss_total)}
                      </td>
                      <td className={`px-4 py-3 text-sm font-medium
                        ${(pos.unrealized_pnl || 0) >= 0
                          ? "text-green-400" : "text-red-400"}`}>
                        {fmt$(pos.unrealized_pnl)}
                      </td>
                      <td className="px-4 py-3 text-gray-400 text-sm">
                        {pos.expiration_date}
                      </td>
                      <td className="px-4 py-3">
                        <button
                          onClick={() => setSelectedPosition(pos)}
                          className="text-blue-400 hover:text-blue-300
                            text-xs transition-colors"
                        >
                          Payoff
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* Selected Position Payoff */}
        {selectedPosition && (
          <section className="mb-8">
            <div className="bg-gray-800 rounded-lg p-6">
              <div className="flex justify-between items-start mb-2">
                <h2 className="text-lg font-semibold text-gray-200">
                  {selectedPosition.ticker} —{" "}
                  {selectedPosition.strategy?.replace(/_/g, " ")}
                </h2>
                <button
                  onClick={() => setSelectedPosition(null)}
                  className="text-gray-500 hover:text-white text-sm"
                >
                  ✕
                </button>
              </div>
              <GreeksPanel
                greeks={{
                  delta: selectedPosition.entry_delta,
                  gamma: selectedPosition.entry_gamma,
                  theta: selectedPosition.entry_theta,
                  vega: selectedPosition.entry_vega,
                }}
                strategy={selectedPosition.strategy}
              />
              <PayoffDiagram
                strategy={selectedPosition.strategy}
                strikes={
                  Array.isArray(selectedPosition.legs)
                    ? selectedPosition.legs.reduce((acc, leg) => {
                        if (leg.type === "call" && leg.side === "buy")
                          acc.strike = leg.strike;
                        if (leg.type === "put" && leg.side === "sell")
                          acc.short_strike = leg.strike;
                        if (leg.type === "put" && leg.side === "buy")
                          acc.long_strike = leg.strike;
                        return acc;
                      }, {})
                    : {}
                }
                currentPrice={
                  selectedPosition.entry_price
                    ? selectedPosition.entry_price * 50 + 100
                    : 150
                }
                iv={0.25}
              />
            </div>
          </section>
        )}

      </div>
    </div>
  );
}
