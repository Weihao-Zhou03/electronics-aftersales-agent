.PHONY: dev prepare bootstrap init-demo smoke check test-domain test-integration test-e2e test-e2e-fixed demo-scenarios eval-dev eval-holdout backup restore-smoke doctor
COMPOSE = docker compose --env-file .env --env-file .env.chatwoot
EVAL_REVISION ?= v2
export PYTHONPATH := $(CURDIR)/src:$(CURDIR)

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
	uv run mypy src/aftersales src/demo_provider
	uv run pytest -m "not postgres and not live"
	uv run python scripts/doctor.py --compose-only --example
	npm ci --prefix frontend
	npm run build --prefix frontend

test-domain:
	uv run pytest tests/test_domain.py tests/test_operations_worker.py tests/test_provider.py -m "not postgres and not live"

test-integration:
	uv run pytest -m postgres

test-e2e:
	npm ci --prefix tests/e2e
	npm test --prefix tests/e2e -- business.spec.js

test-e2e-fixed:
	npm ci --prefix tests/e2e
	npm test --prefix tests/e2e -- widget.spec.js

demo-scenarios:
	uv run python scripts/run_demo_scenarios.py --execute-demo

backup:
	uv run python scripts/backup.py

restore-smoke:
	uv run python scripts/restore_smoke.py "$(BACKUP_DIR)" --cleanup

eval-dev:
	@test "$(MODEL_MODE)" = "live" || (echo "Set MODEL_MODE=live explicitly; this uses the configured real model quota."; exit 2)
	uv run python scripts/evaluate.py --revision $(EVAL_REVISION) --split dev

eval-holdout:
	@test "$(MODEL_MODE)" = "live" || (echo "Set MODEL_MODE=live explicitly; freeze a passing dev run first."; exit 2)
	uv run python scripts/evaluate.py --revision $(EVAL_REVISION) --split holdout
