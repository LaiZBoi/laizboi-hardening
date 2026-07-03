# Docker support files

Operator guide: **[`docs/docker.md`](../docs/docker.md)**

| Path | Purpose |
|------|---------|
| `nginx/` | Nginx config for `docker compose --profile proxy` |
| `mariadb/conf.d/` | MariaDB tuning snippets |

Quick start from repo root:

```bash
cp .env.example .env
docker compose up -d --build
```

Production with Nginx (gunicorn not on host):

```bash
make docker-up-proxy
```
