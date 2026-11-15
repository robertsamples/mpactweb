# Caddy with the documentation site and the landing page built in.
#
# Built from the repo root (see docker-compose.prod.yml), so the docs are
# rebuilt from docs/ and mkdocs.yml every time the image is:
#
#   docker compose -f deploy/docker-compose.prod.yml build caddy
#   docker compose -f deploy/docker-compose.prod.yml up -d caddy

FROM python:3.12-slim AS docs
RUN pip install --no-cache-dir zensical==0.0.57
WORKDIR /src
COPY mkdocs.yml ./
COPY docs/ docs/
RUN zensical build --clean

FROM caddy:2-alpine
COPY --from=docs /src/site /srv/docs
COPY deploy/landing/ /srv/landing/
COPY web/assets/mpact-icon.png /srv/landing/mpact-icon.png
COPY web/favicon.ico /srv/landing/favicon.ico
