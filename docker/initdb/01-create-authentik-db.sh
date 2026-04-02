#!/bin/bash
set -e

# Create the Authentik database and user.
# This runs automatically on first Postgres start via docker-entrypoint-initdb.d.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    CREATE USER authentik WITH PASSWORD 'authentik-local-dev';
    CREATE DATABASE authentik OWNER authentik;
    GRANT ALL PRIVILEGES ON DATABASE authentik TO authentik;
EOSQL
