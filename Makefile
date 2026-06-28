# Client St0r — VPS operator shortcuts.
# Run `make help` for targets. Full guide: docs/deployment-vps.md

.DEFAULT_GOAL := help
VENV ?= /opt/clientst0r/venv
PY   ?= $(VENV)/bin/python
ENV  ?= /etc/clientst0r/.env

help: ## Show this help
	@awk 'BEGIN {FS = ":.*##"; printf "\nUsage: make \033[36m<target>\033[0m\n\nTargets:\n"} \
	     /^[a-zA-Z0-9_-]+:.*##/ { printf "  \033[36m%-22s\033[0m %s\n", $$1, $$2 }' $(MAKEFILE_LIST)

check: ## Django system checks
	set -a && . $(ENV) && set +a && $(PY) manage.py check --deploy

check-safe: ## Privacy/security deployment audit (exits non-zero on FAIL)
	set -a && . $(ENV) && set +a && $(PY) manage.py check_safe_deployment

test-hardening: ## Run hardening + updater tests
	set -a && . $(ENV) && set +a && $(PY) manage.py test core.tests.test_hardening_gates core.tests.test_updater -v2

migrate: ## Apply database migrations
	set -a && . $(ENV) && set +a && $(PY) manage.py migrate --noinput

collectstatic: ## Collect static files
	set -a && . $(ENV) && set +a && $(PY) manage.py collectstatic --noinput

backup-db: ## Dump MariaDB to /var/backups/clientst0r/
	@sudo mkdir -p /var/backups/clientst0r
	@set -a && . $(ENV) && set +a && \
	  mariadb-dump -h "$$DB_HOST" -u "$$DB_USER" -p"$$DB_PASSWORD" "$$DB_NAME" \
	  | gzip > /var/backups/clientst0r/clientst0r-$$(date +%F).sql.gz && \
	  echo "Wrote /var/backups/clientst0r/clientst0r-$$(date +%F).sql.gz"

.PHONY: help check check-safe test-hardening migrate collectstatic backup-db
