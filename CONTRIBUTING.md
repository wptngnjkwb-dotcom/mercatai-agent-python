# Contributing to mercatai-agent

Thank you for your interest in improving the official Python SDK for the [Mercatai](https://mercatai.eu) AI agent marketplace!

## Ways to contribute

- **Bug reports** — open an issue with a minimal reproducible example
- **Feature requests** — open an issue describing the use-case
- **Pull requests** — see the workflow below

## Development setup

```bash
git clone https://github.com/wptngnjkwb-dotcom/mercatai-agent-python.git
cd mercatai-agent-python

# create a virtual environment (Python 3.10+)
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# install dev dependencies
pip install -e ".[all]"
pip install pytest pytest-cov
```

## Running tests

```bash
pytest tests/ -v
```

Tests mock all HTTP calls — no real API key needed.

## Pull request checklist

- [ ] Branch off `main`, name it `fix/…` or `feat/…`
- [ ] Add / update tests for new behaviour
- [ ] `pytest` passes locally
- [ ] Docstrings follow existing style (NumPy-style parameters section)
- [ ] Update `CHANGELOG.md` (add a line under `## [Unreleased]`)

## Code style

The project uses standard Python conventions — no linter is enforced yet, but please follow PEP 8 and keep lines under 100 characters.

## Reporting security issues

Please **do not** open a public issue for security vulnerabilities. Email [dev@mercatai.eu](mailto:dev@mercatai.eu) instead. We aim to respond within 48 hours.

## License

By contributing you agree that your changes will be released under the [MIT License](LICENSE).
