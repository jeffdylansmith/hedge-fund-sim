-- Migration: RAG document store with pgvector
-- Requires pgvector extension (enabled via Supabase dashboard or SQL below)

CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS rag_documents (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),

  -- Source tracking
  source_type text NOT NULL,        -- 'options_episode', 'news', 'analysis'
  source_id uuid,                   -- FK to source record (options_positions.id etc)
  trader_id text,
  ticker text,

  -- Content
  content text NOT NULL,            -- the narrative text that gets embedded
  metadata jsonb DEFAULT '{}',      -- structured data for filtered retrieval

  -- Embedding
  embedding vector(1536),           -- dimension matches text-embedding-3-small
  embedding_model text DEFAULT 'text-embedding-3-small',

  -- Temporal
  created_at timestamptz DEFAULT now(),
  event_date date                   -- date the underlying event occurred
);

-- Indexes
CREATE INDEX IF NOT EXISTS idx_rag_documents_source
  ON rag_documents(source_type, ticker);

CREATE INDEX IF NOT EXISTS idx_rag_documents_trader
  ON rag_documents(trader_id, source_type);

CREATE INDEX IF NOT EXISTS idx_rag_documents_event_date
  ON rag_documents(event_date DESC);

-- pgvector similarity search index (cosine distance)
CREATE INDEX IF NOT EXISTS idx_rag_documents_embedding
  ON rag_documents USING ivfflat (embedding vector_cosine_ops)
  WITH (lists = 100);
