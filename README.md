# FireBIM API server
[![Build & Push Image](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/build-image.yml/badge.svg)](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/build-image.yml)
[![CI](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/ci.yml/badge.svg)](https://github.com/FireBIM-org/firebim-api-server/actions/workflows/ci.yml)


This repository is solely for API-specific code for deployment to a server.
FireBIM business logic does **not** belong in this repository.
This repository depends on on other FireBIM-repositories for the logic. E.g. `firebim-api-server` is bare on its own.

## Prerequisites

- **Python 3.12** or higher
- **UV** package manager ([installation guide](https://docs.astral.sh/uv/))
- **Docker**

## Getting started (using Docker Compose)

1. Clone the repository
2. Copy `.env.example` to `.env`
3. Start all services:

```bash
make dev-up
```

4. Access the services:
   - Backend API: http://localhost:8000
   - Swagger UI: http://localhost:8000/docs
   - Authentik Admin: http://localhost:9080 (akadmin/Password123! — auto-initialized)
   - RabbitMQ Management: http://localhost:15672 (firebim/firebim)
   - MinIO Console: http://localhost:9001 (minioadmin/minioadmin)
   - Mailpit (email UI): http://localhost:8025


## Deployment
This repository can be deployed for self-hosting, or you can use one of the offical API-endpoints.

The official deployment is available at:
- [api.firebim.org](https://api.firebim.org/docs)
- [api.staging.firebim.org](https://api.staging.firebim.org)


### Production

**Important!** Docker compose files in this repository are only intended for local development.
Do not use included compose files for for production deployment.
