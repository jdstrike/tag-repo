# tag-repo

Two services hosted on the Contabo VPS at `tag-repo.com` via Dokploy.

| Service | URL | Purpose |
|---|---|---|
| `apps/report-uploader` | `https://tag-repo.com` | Flask app accepting report uploads under `/reports/upload`, listing under `/reports/<id>` |
| `apps/context-mirror` | `https://context.tag-repo.com` | Static Caddy mirror of `tag.schatt.me` — brand & AI context docs, design system, templates |

## Deploy

Both services run as Dokploy applications backed by a **local Docker registry on `127.0.0.1:5000`** (Dokploy's `Source: Docker` always pulls — see ops note below). To redeploy a service:

```sh
cd apps/<service>
docker build -t 127.0.0.1:5000/<service>:1 .
docker push 127.0.0.1:5000/<service>:1
# then trigger redeploy in Dokploy (or via API)
```

## Refresh the context mirror

```sh
cd apps/context-mirror
./refresh.sh
docker build -t 127.0.0.1:5000/context-mirror:1 .
docker push 127.0.0.1:5000/context-mirror:1
```

## Why a local registry

Dokploy's `Source: Docker` mode always runs `docker pull`, even when the image already exists on the host. Pushing to `127.0.0.1:5000` makes the image pullable without a public registry. Docker treats `127.0.0.0/8` as insecure-allowed by default, so no daemon config is needed.
