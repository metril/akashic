.PHONY: up down restart web api scanner watch logs ps build bootstrap-scanner

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

scanner:
	docker compose build scanner && docker compose --profile scanner up -d scanner

# Mint a `default`-pool scanner via the api's CLI helper, write the
# private key to secrets/default-scanner/scanner.key, and append the
# scanner id to .env so `docker compose --profile scanner up scanner`
# Just Works after a fresh checkout. Requires the api to be up + an
# admin user to already exist.
bootstrap-scanner:
	@mkdir -p secrets/default-scanner
	@docker compose exec -T api python -m akashic.tools.bootstrap_scanner \
	    --name default --pool default \
	    --key-out /tmp/scanner.key \
	    > /tmp/bootstrap.json
	@docker compose exec -T api cat /tmp/scanner.key \
	    > secrets/default-scanner/scanner.key
	@chmod 600 secrets/default-scanner/scanner.key
	@SID=$$(jq -r .id /tmp/bootstrap.json); \
	    grep -v '^SCANNER_ID=' .env > .env.tmp 2>/dev/null || true; \
	    mv .env.tmp .env 2>/dev/null || true; \
	    echo "SCANNER_ID=$$SID" >> .env; \
	    echo "Bootstrapped scanner $$SID. Run: make scanner"

watch:
	docker compose watch

logs:
	docker compose logs -f api web

ps:
	docker compose ps
