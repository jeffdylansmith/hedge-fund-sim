import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Ensure project root is on path
sys.path.insert(0, "/Users/dylan/hedge-fund-sim")

# --- Patch supabase at import time before any app module is loaded ---
# db.py calls create_client() at module level, so we must patch before import
import unittest.mock as mock
_supabase_patcher = mock.patch("supabase.create_client", return_value=MagicMock())
_supabase_patcher.start()

@pytest.fixture(autouse=False)
def mock_supabase():
    """Returns a MagicMock standing in for the supabase client."""
    client = MagicMock()
    with patch("utils.db.supabase", client):
        yield client

@pytest.fixture(autouse=False)
def mock_llm():
    """
    Returns a factory that patches the anthropic client to return
    a deterministic canned response. Pass the response text you want.
    Usage: mock_llm("your canned response text")
    """
    def _make(response_text: str = '{"risk_score": 5, "recommendation": "proceed", "flags": [], "rationale": "test"}'):
        mock_client = MagicMock()
        mock_message = MagicMock()
        mock_message.content = [MagicMock(text=response_text)]
        mock_client.messages.create.return_value = mock_message
        return mock_client
    return _make
