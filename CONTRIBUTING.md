# Contributing

## Development setup

```bash
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,game]"
pytest
```

## Pull requests

- Keep domain-specific rules in adapters, not in the generic planner.
- Add tests for probability-mass conservation, terminal verification, and any new pruning behavior.
- Never use a reverse answer oracle in an end-to-end planner mode.
- Report pruned probability as `unresolved`; do not silently renormalize it away.
- Do not commit API keys, `.env` files, provider logs, or paid-response artifacts.
- Keep the public API typed and compatible with Python 3.10–3.12.

Before opening a PR, run `pytest` and `python -m build`.
