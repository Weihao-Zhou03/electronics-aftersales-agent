.PHONY: dev prepare bootstrap init-demo smoke check test-domain test-integration test-e2e eval-dev eval-holdout backup restore-smoke doctor
COMPOSE = docker compose --env-file .env --env-file .env.chatwoot
export PYTHONPATH := $(CURDIR)/src

dev: prepare
	$(COMPOSE) up -d api worker chatwoot-rails chatwoot-sidekiq

prepare:
	$(COMPOSE) up -d --wait postgres redis
	$(COMPOSE) run --rm --no-deps chatwoot-rails bundle exec rails db:chatwoot_prepare
	$(COMPOSE) build api
	$(COMPOSE) run --rm --no-deps migrate

bootstrap:
	uv run python scripts/bootstrap_chatwoot.py

init-demo:
	$(COMPOSE) run --rm --no-deps -e M0_DEMO_PASSWORD api uv run --frozen --no-dev python -m aftersales.auth seed

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

eval-dev eval-holdout backup restore-smoke:
	@echo "$@ is outside authorized M0; not implemented or validated."
	@exit 2
