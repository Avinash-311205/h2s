# Module 1: Citizen Ingestion Service

This module provides a clean, production-ready ingestion layer for the Niti-Setu / Jan-Setu platform.

## Included

- FastAPI backend for citizen request ingestion and media uploads
- SQLAlchemy metadata model for standardized request records
- Redis event publication for downstream Module 2 consumption
- MinIO-compatible object storage abstraction for image/audio uploads
- Simple React frontend to submit text, image, audio, and location
- Local Docker Compose environment for backend, frontend, PostgreSQL, Redis, and MinIO

## Quick start

```bash
docker compose up --build
```

Then access:

- Frontend: http://localhost:3000
- API docs: http://localhost:8000/docs
- MinIO console: http://localhost:9001

## API overview

- POST /api/v1/requests
- POST /api/v1/requests/{request_id}/media
- GET /api/v1/requests/{request_id}
- GET /api/v1/health
