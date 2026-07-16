.PHONY: help install-backend install-mobile dev-backend dev-bot dev-mobile
.PHONY: docker-up docker-down docker-build db-migrate db-rollback
.PHONY: lint test test-e2e clean

## —— Auri Makefile ———————————————————————————————————————————————————————————
help: ## Show this help
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

## —— Installation ————————————————————————————————————————————————————————————

install-backend: ## Install Python backend dependencies
	pip install -r backend/requirements.txt
	pip install -e backend/

install-mobile: ## Install mobile app dependencies
	cd mobile && npm install

## —— Development Servers ——————————————————————————————————————————————————————

dev-backend: ## Run FastAPI backend in development mode
	uvicorn backend.main:app --reload --host 0.0.0.0 --port 8000

dev-bot: ## Run Telegram bot in polling mode (development)
	python -m bot.main

dev-mobile: ## Start Expo mobile dev server
	cd mobile && npx expo start

## —— Docker ————————————————————————————————————————————————————————————————————

docker-up: ## Start all Docker services
	docker compose up -d

docker-down: ## Stop all Docker services
	docker compose down

docker-build: ## Build all Docker images without cache
	docker compose build --no-cache

docker-logs: ## Tail logs from all services
	docker compose logs -f

## —— Database ——————————————————————————————————————————————————————————————————

db-migrate: ## Run Alembic migrations
	cd backend && alembic upgrade head

db-rollback: ## Rollback last Alembic migration
	cd backend && alembic downgrade -1

db-revision: ## Create a new Alembic migration (usage: make db-revision msg="description")
	cd backend && alembic revision --autogenerate -m "$(msg)"

## —— Linting & Formatting ——————————————————————————————————————————————————————

lint: ## Run all linters
	ruff check backend/ bot/
	ruff format --check backend/ bot/
	mypy backend/ bot/ --ignore-missing-imports
	cd mobile && npx eslint . --ext .ts,.tsx && npx tsc --noEmit

lint-fix: ## Auto-fix lint issues
	ruff check --fix backend/ bot/
	ruff format backend/ bot/

## —— Testing ————————————————————————————————————————————————————————————————————

test: ## Run all Python tests
	pytest backend/ bot/ -v --cov=backend --cov=bot --cov-report=term-missing

test-e2e: ## Run end-to-end tests
	pytest tests/e2e/ -v

## —— Cleanup ————————————————————————————————————————————————————————————————————

clean: ## Remove all build artifacts, caches, and virtual envs
	rm -rf __pycache__ .pytest_cache .mypy_cache *.egg-info
	rm -rf backend/__pycache__ bot/__pycache__
	rm -rf .venv venv
	rm -rf mobile/node_modules mobile/.expo mobile/web-build
	rm -rf dist build
	find . -name '*.pyc' -delete
	find . -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
	@echo "✨ Clean complete"
