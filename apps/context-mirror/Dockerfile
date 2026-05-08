FROM caddy:2.10-alpine

COPY Caddyfile /etc/caddy/Caddyfile
COPY content /srv

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
  CMD wget -q -O - http://127.0.0.1/healthz | grep -q ok || exit 1
