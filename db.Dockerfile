# Postgres 18 + PostGIS 3, built on the official image so every benchmark
# (baseline, fixes, Explorer, spatial) runs on the same server build.
# The official image already has the PostgreSQL apt repository configured.
FROM postgres:18

RUN apt-get update \
 && apt-get install -y --no-install-recommends postgresql-18-postgis-3 \
 && rm -rf /var/lib/apt/lists/*
