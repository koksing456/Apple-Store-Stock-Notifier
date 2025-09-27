# Repository Guidelines

## Project Structure & Module Organization
- Core runtime lives in `monitor.py`, `store_checker.py`, and `remote.py`; these orchestrate polling Apple APIs and notifying Telegram users.
- The NiceGUI interface launches from `app.py`, with static assets under `assets/` and shared helpers in `utils.py` and `interface.py`.
- Configuration defaults sit in `config.toml` at the repo root; tests mirror those fixtures in `test/config.toml` alongside pytest suites in `test/`.

## Build, Test, and Development Commands
- `python -m venv .venv && source .venv/bin/activate` creates an isolated environment (required before installing dependencies).
- `pip install -r requirements.txt` installs runtime, GUI, and testing dependencies.
- `python remote.py` runs the headless notifier; `python app.py` serves the NiceGUI dashboard for local monitoring.
- `nicegui-pack --windowed --name "Apple Stock Notifier" app.py` bundles the desktop app when you need a distributable.

## Coding Style & Naming Conventions
- Follow Black’s defaults (88-char lines, 4-space indents); run `black .` before committing. Use `isort .` to keep imports grouped and alphabetized.
- Module and package names stay lowercase with underscores (`confighandler.py` is historical—prefer `config_handler.py` for new files).
- Prefer descriptive function names (`start_monitoring`) and PascalCase for classes (`ConfigHandler`). Keep async coroutines suffixed with verbs (`restart_handler`).

## Testing Guidelines
- Use pytest; structure new tests under `test/` as `test_<module>.py`. Mirror config fixtures via TOML files in the same directory.
- Run `pytest` locally; add `pytest --cov=.` before raising a PR to check regression coverage.
- When adding I/O heavy tests, mock network calls (see `test/test_confighandler.py` for patterns) to keep suites fast.

## Commit & Pull Request Guidelines
- Match the repository’s history: start commit subjects with a capitalized verb (`Implement`, `Create`) and keep them under 72 characters.
- Reference related issues in the body (`Refs #42`) and summarize behavior changes plus config impacts.
- PRs should include: purpose, testing evidence (`pytest` output or screenshots for GUI), and any deployment steps. Tag reviewers familiar with Telegram or NiceGUI changes.

## Configuration & Security Tips
- Never commit secrets; populate `config.toml` with placeholders and rely on environment overrides where possible.
- For production deployments, rotate Telegram tokens regularly and consider proxy whitelists when enabling `randomize_proxies`.
