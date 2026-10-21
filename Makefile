.PHONY: dev prepare bootstrap init-demo smoke check test-domain test-integration test-e2e eval-dev eval-holdout backup restore-smoke doctor
COMPOSE = docker compose --env-file .env --env-file .env.chatwoot
export PYTHONPATH := $(CURDIR)/src

dev:
	uv run python scripts/dev.py

prepare:
	uv run python scripts/dev.py --prepare-only

bootstrap:
	uv run python scripts/bootstrap_chatwoot.py

init-demo:
	$(COMPOSE) run --rm --no-deps -e M0_DEMO_PASSWORD api uv run --frozen --no-sync --no-dev python -m aftersales.demo_seed

smoke:
	uv run python scripts/live_probe.py

doctor:
	uv run python scripts/doctor.py

check:
	uv sync --frozen
	uv run ruff check .
	uv run pytest -m "not postgres and not live"
	uv run python scripts/doctor.py --compose-only --example

test-domain:
	@echo "M0 does not implement M1 domain logic. No domain acceptance claimed."
	@exit 2

test-integration:
	uv run pytest -m postgres

test-e2e:
	@echo "Browser acceptance is manual in M0. Follow docs/HANDOFF.md; no automated e2e result claimed."
	@exit 2

backup:
	uv run python scripts/backup.py

restore-smoke:
	uv run python scripts/restore_smoke.py "$(BACKUP_DIR)" --cleanup

eval-dev eval-holdout:
	@echo "$@ is outside authorized M0; not implemented or validated."
	@exit 2
