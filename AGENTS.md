# Repository Guidelines

## Project Structure & Module Organization

The installable package is `src/substack_publisher_mcp/`. `server.py` defines MCP tools and the stdio entry point; `pending.py` handles mutation confirmations; `auth.py` and `setup_auth.py` manage credentials; `live_verify.py` contains the separately invoked compatibility qualifier. Tests live in `tests/`, with files named `test_<area>.py`. Package metadata and pytest settings are in `pyproject.toml`; `uv.lock` records the resolved dependency set. CI configuration is in `.github/workflows/`.

## Build, Test, and Development Commands

- `uv sync --locked --extra dev --python 3.11` installs the locked development environment.
- `uv run --locked --extra dev pytest -q` runs the offline test suite.
- `uv run --locked --extra dev pytest -q tests/test_startup.py` checks CLI startup and the MCP handshake.
- `uv run --locked substack-publisher-mcp` launches the stdio server for an MCP client.
- `uv run --isolated --python 3.10 --locked --extra dev pytest -q` checks the minimum supported Python version.

Do not run the live qualifier as a routine test: `substack-publisher-mcp-qualify` makes external requests only when explicitly passed `--live`.

## Coding Style & Naming Conventions

Use Python 3.10-compatible syntax, four spaces for indentation, and standard `snake_case` names for modules, functions, and variables. Keep MCP tool validation explicit and preserve the existing confirmation flow for every remote mutation. Tests should be focused, deterministic, and use mocks or synthetic HTTP responses rather than real Substack requests. No formatter, linter, or type checker is configured.

## Testing Guidelines

Add or update `tests/test_<area>.py` for behavior changes. Run the full pytest command before submitting when practical. Tests must not require credentials, publish content, or contact Substack; the startup smoke test exercises the installed CLI offline. CI runs the locked suite on Python 3.11 on Linux and Windows.

## Commit & Pull Request Guidelines

Recent commits use short, imperative subjects (for example, “Harden offline release verification”). Follow that style and keep each commit focused. For substantial changes, open an issue first as the README recommends. Pull requests should explain behavior and security implications, link the issue when applicable, and include relevant test output. Call out dependency or lockfile changes explicitly.

## Security & Configuration

Never commit cookies, passwords, or keyring data. Keep credentials in the OS keyring or environment variables as documented in `README.md`. Treat live qualification as an explicitly authorized external operation; do not add it to ordinary tests or CI.