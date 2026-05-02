.PHONY: up down restart web api scanner watch logs ps build bootstrap-scanner bootstrap-scanner-legacy

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

# v0.3.0 flow: mint a join token via the api admin CLI, run
# `akashic-scanner claim` inside the scanner image (so the keypair
# is generated inside the scanner container — correct trust
# boundary), write the resulting private key + scanner id back to
# secrets/default-scanner/. The pre-v0.3.0 path (api-side keypair)
# lives in `bootstrap-scanner-legacy` if you need it.
bootstrap-scanner:
	@mkdir -p secrets/default-scanner
	@docker compose build scanner > /dev/null
	@docker compose exec -T api python -m akashic.tools.mint_claim_token \
	    --label default --pool default --ttl-minutes 10 \
	    > /tmp/claim.json
	@TOKEN=$$(jq -r .token /tmp/claim.json); \
	    docker compose --profile scanner run --rm \
	      -v $$(pwd)/secrets/default-scanner:/secrets \
	      scanner claim \
	      --api=http://api:8000 \
	      --token=$$TOKEN \
	      --key=/secrets/scanner.key \
	      --id-file=/secrets/scanner.id ; \
	    chmod 600 secrets/default-scanner/scanner.key ; \
	    SID=$$(cat secrets/default-scanner/scanner.id) ; \
	    grep -v '^SCANNER_ID=' .env > .env.tmp 2>/dev/null || true; \
	    mv .env.tmp .env 2>/dev/null || true; \
	    echo "SCANNER_ID=$$SID" >> .env; \
	    echo "Bootstrapped scanner $$SID. Run: make scanner"

# Pre-v0.3.0 flow — api generates the keypair, we copy the private
# key to the host. Kept around for scripted automation that already
# depends on it.
bootstrap-scanner-legacy:
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
	    echo "Bootstrapped scanner $$SID via legacy api-side keypair. Run: make scanner"

watch:
	docker compose watch

logs:
	docker compose logs -f api web

ps:
	docker compose ps
