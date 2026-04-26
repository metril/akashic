.PHONY: up down restart web api watch logs ps build

up:
	docker compose up -d

down:
	docker compose down

restart:
	docker compose restart

build:
	docker compose build

web:
	docker compose build web && docker compose up -d web

api:
	docker compose build api && docker compose up -d api

watch:
	docker compose watch

logs:
	docker compose logs -f api web

ps:
	docker compose ps
