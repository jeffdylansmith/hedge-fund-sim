-- Migration: Add options positions tracking
-- Safe to run on existing database — adds new table and column only
-- Does not touch fund_balance, portfolio_positions, or trades tables

-- Add options allocation cap to fund_balance for runtime visibility
-- (trader_config.py is source of truth, this is for reporting only)
ALTER TABLE fund_balance
ADD COLUMN IF NOT EXISTS options_risk_cap_pct FLOAT DEFAULT 0.15;

-- Options positions table
CREATE TABLE IF NOT EXISTS options_positions (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  trader_id text NOT NULL,
  ticker text NOT NULL,
  strategy text NOT NULL,
  status text DEFAULT 'open',

  -- Position structure (flexible across all strategy types)
  legs jsonb NOT NULL DEFAULT '[]',

  -- Risk metrics captured at entry
  max_loss_per_contract float NOT NULL,
  contracts int NOT NULL DEFAULT 1,
  net_credit_debit float NOT NULL DEFAULT 0,
  max_loss_total float NOT NULL,

  -- Greeks at entry (snapshot)
  entry_delta float,
  entry_theta float,
  entry_vega float,
  entry_gamma float,

  -- Pricing
  entry_price float,
  current_price float,
  unrealized_pnl float DEFAULT 0,
  realized_pnl float,

  -- Lifecycle
  opened_at timestamptz DEFAULT now(),
  expiration_date date,
  closed_at timestamptz,
  close_reason text
);

-- Index for common query patterns
CREATE INDEX IF NOT EXISTS idx_options_positions_trader
  ON options_positions(trader_id, status);

CREATE INDEX IF NOT EXISTS idx_options_positions_ticker
  ON options_positions(ticker, status);
