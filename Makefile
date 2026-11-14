.PHONY: dev stack check test e2e diagrams

dev:      ## только инфраструктура, код запускается локально
	docker compose up -d --wait db redis broker

stack:    ## всё в контейнерах
	test -f .env || cp .env.example .env
	docker compose up -d --build --wait

check:
	uv run ruff check . && uv run ruff format --check . && uv run mypy src tests

test:     ## нужны Postgres и Redis: make dev
	uv run pytest --cov

e2e:      ## против поднятого make stack
	uv run python scripts/e2e.py

diagrams: ## перегенерировать схемы для README и проверить подписи
	python tools/docs-diagrams/c4.py
	python tools/docs-diagrams/check-labels.py
