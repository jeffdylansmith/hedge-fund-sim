.PHONY: test test-unit test-agents test-integration test-watch

test:
	PYTHONPATH=/Users/dylan/hedge-fund-sim venv/bin/pytest tests/ -v

test-unit:
	PYTHONPATH=/Users/dylan/hedge-fund-sim venv/bin/pytest tests/unit/ -v

test-agents:
	PYTHONPATH=/Users/dylan/hedge-fund-sim venv/bin/pytest tests/agents/ -v

test-integration:
	PYTHONPATH=/Users/dylan/hedge-fund-sim venv/bin/pytest tests/integration/ -v

test-watch:
	PYTHONPATH=/Users/dylan/hedge-fund-sim venv/bin/pytest tests/ -v --tb=short -x
