.PHONY: up down logs test test-pg fmt seed-reset

COMPOSE ?= docker compose
PG_TEST_URL ?= postgresql+psycopg://ach:ach@localhost:$${ACH_DB_HOST_PORT:-5433}/ach

up: ## Build and start the local stack (UI :8686, API :8600)
	$(COMPOSE) up -d --build

down: ## Stop the stack (keeps the database volume)
	$(COMPOSE) down

logs: ## Follow logs from all services
	$(COMPOSE) logs -f --tail=200

test: ## Backend tests against SQLite
	cd backend && python -m pytest -q

test-pg: ## Backend tests against the compose Postgres (starts db if needed)
	$(COMPOSE) up -d --wait db
	cd backend && ACH_TEST_DATABASE_URL=$(PG_TEST_URL) python -m pytest -q

fmt: ## Format Terraform
	terraform -chdir=infra/terraform fmt -recursive

seed-reset: ## Wipe the database volume and restart with fresh demo data
	$(COMPOSE) down -v
	$(COMPOSE) up -d --build
