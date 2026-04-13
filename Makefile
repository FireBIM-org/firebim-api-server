.PHONY: help install dev test lint format openapi dev-up docker-down docker-build logs migrate

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

install: ## Install all dependencies (including dev and worker groups)
	uv sync --all-groups

dev: ## Run the API dev server with auto-reload
	uv run fastapi dev app/main.py

test: ## Run tests with coverage
	uv run pytest tests/ --cov=app --cov-report=term-missing

lint: ## Check code with ruff
	uv run ruff check .

format: ## Format and auto-fix code with ruff
	uv run ruff format .
	uv run ruff check --fix .

openapi: ## Generate openapi.json from the app
	uv run python scripts/generate_openai.py

dev-up: ## Start all services with docker compose
	docker compose up -d

docker-down: ## Stop all services
	docker compose down

docker-build: ## Rebuild API and worker images
	docker compose build api celery-worker

logs: ## Tail logs for the API service (override SERVICE= for others)
	docker compose logs -f $(or $(SERVICE),api)

migrate: ## Run alembic database migrations
	uv run alembic upgrade head
