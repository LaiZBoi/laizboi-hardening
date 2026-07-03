# Client St0r — operator shortcuts.
# Run `make help` for targets.

COMPOSE ?= docker compose
VENV    ?= /opt/clientst0r/venv
PY      ?= $(VENV)/bin/python
ENV     ?= /etc/clientst0r/.env

.DEFAULT_GOAL := help

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	     /^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

# ─── Docker ──────────────────────────────────────────────────────────

docker-build: ## Build the production image locally
	$(COMPOSE) build app

docker-up: ## Start app + db in detached mode
	$(COMPOSE) up -d
	@echo ""
	@echo "Client St0r is starting. Tail logs with: make docker-logs"
	@echo "App will be reachable at: http://localhost:$${WEB_PORT:-8000}"

docker-up-proxy: ## Start app + db + nginx; gunicorn not published on host
	$(COMPOSE) -f docker-compose.yml -f docker-compose.proxy.yml --profile proxy up -d

docker-down: ## Stop containers (volumes preserved)
	$(COMPOSE) down

docker-down-clean: ## Stop containers AND wipe volumes (DESTRUCTIVE)
	@echo "This deletes the database, uploads, and static files."
	@read -p "Type YES to confirm: " ans && [ "$$ans" = "YES" ] || (echo "Aborted." && exit 1)
	$(COMPOSE) down -v

docker-logs: ## Tail logs from the app container
	$(COMPOSE) logs -f app

docker-shell: ## Open a bash shell inside the running app container
	$(COMPOSE) exec app bash

docker-migrate: ## Run Django migrations inside the running container
	$(COMPOSE) exec app python manage.py migrate

docker-createsuperuser: ## Create a Django superuser interactively
	$(COMPOSE) exec app python manage.py createsuperuser

docker-check-safe: ## Privacy/security audit inside the app container
	$(COMPOSE) exec app python manage.py check_safe_deployment

dev-up: ## Start with the dev override (source mount + reload)
	$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml up

dev-down: ## Stop the dev stack
	$(COMPOSE) -f docker-compose.yml -f docker-compose.dev.yml down

docker-backup: ## Dump the database to ./backups/clientst0r-YYYYMMDD.sql.gz
	@mkdir -p backups
	@stamp=$$(date +%Y%m%d-%H%M%S); \
	echo "Dumping db to backups/clientst0r-$$stamp.sql.gz..."; \
	$(COMPOSE) exec -T db sh -c \
	  'mariadb-dump -uroot -p"$$MYSQL_ROOT_PASSWORD" --single-transaction --routines --triggers "$$MYSQL_DATABASE"' \
	  | gzip > backups/clientst0r-$$stamp.sql.gz; \
	echo "Done: backups/clientst0r-$$stamp.sql.gz"

# ─── VPS (bare metal) ────────────────────────────────────────────────

check: ## Django system checks (VPS)
	set -a && . $(ENV) && set +a && $(PY) manage.py check --deploy

check-safe: ## Privacy/security deployment audit (VPS)
	set -a && . $(ENV) && set +a && $(PY) manage.py check_safe_deployment

test-hardening: ## Run hardening + updater tests (VPS)
	set -a && . $(ENV) && set +a && $(PY) manage.py test core.tests.test_hardening_gates core.tests.test_updater -v2

migrate: ## Apply database migrations (VPS)
	set -a && . $(ENV) && set +a && $(PY) manage.py migrate --noinput

collectstatic: ## Collect static files (VPS)
	set -a && . $(ENV) && set +a && $(PY) manage.py collectstatic --noinput

backup-db: ## Dump MariaDB to /var/backups/clientst0r/ (VPS)
	@sudo mkdir -p /var/backups/clientst0r
	@set -a && . $(ENV) && set +a && \
	  mariadb-dump -h "$$DB_HOST" -u "$$DB_USER" -p"$$DB_PASSWORD" "$$DB_NAME" \
	  | gzip > /var/backups/clientst0r/clientst0r-$$(date +%F).sql.gz && \
	  echo "Wrote /var/backups/clientst0r/clientst0r-$$(date +%F).sql.gz"

.PHONY: help docker-build docker-up docker-up-proxy docker-down docker-down-clean \
        docker-logs docker-shell docker-migrate docker-createsuperuser docker-check-safe \
        dev-up dev-down docker-backup check check-safe test-hardening migrate \
        collectstatic backup-db
