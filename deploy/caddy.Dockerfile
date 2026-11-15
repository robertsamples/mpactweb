# Caddy with the mpactms.org landing page built in.
#
# Built from the repo root (see docker-compose.prod.yml):
#
#   docker compose -f deploy/docker-compose.prod.yml build caddy
#   docker compose -f deploy/docker-compose.prod.yml up -d caddy

FROM caddy:2-alpine
COPY deploy/landing/ /srv/landing/
COPY web/assets/mpact-icon.png /srv/landing/mpact-icon.png
COPY web/favicon.ico /srv/landing/favicon.ico
