from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, HTTPException, Request, UploadFile, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.db.base import Base
from app.db.session import engine, get_db
from app.models.request import CitizenRequest  # noqa: F401
from app.repositories.request_repository import RequestRepository
from app.schemas.request import CitizenRequestCreate, CitizenRequestResponse, MediaUploadResponse
from app.services.request_service import RequestService
from app.storage.minio_client import ensure_bucket


@asynccontextmanager
async def lifespan(_: FastAPI):
    Base.metadata.create_all(bind=engine)
    ensure_bucket(settings.minio_bucket_name)
    yield


app = FastAPI(
    title="Niti-Setu Citizen Ingestion Service",
    description="Module 1 for receiving and standardizing citizen requests.",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(_: Request, exc: RequestValidationError):
    detail = exc.errors()[0]["msg"] if exc.errors() else "Invalid request payload."
    return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": detail})


@app.get("/api/v1/health")
def health_check():
    return {"status": "ok", "service": "citizen-ingestion"}


@app.post("/api/v1/requests", response_model=CitizenRequestResponse, status_code=status.HTTP_201_CREATED)
def create_request(payload: CitizenRequestCreate, db=Depends(get_db)):
    try:
        service = RequestService(RequestRepository(db))
        request = service.create_request(payload.model_dump(exclude_none=True))
        return request
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@app.post("/api/v1/requests/{request_id}/media", response_model=MediaUploadResponse)
async def upload_media(request_id: str, file: UploadFile = File(...), db=Depends(get_db)):
    if not file.filename:
        raise HTTPException(status_code=400, detail="A filename is required for media upload.")

    content_type = file.content_type or ""
    if content_type.startswith("image/"):
        media_type = "image"
    elif content_type.startswith("audio/"):
        media_type = "audio"
    else:
        filename = (file.filename or "").lower()
        if filename.endswith((".png", ".jpg", ".jpeg", ".webp")):
            media_type = "image"
        elif filename.endswith((".mp3", ".wav", ".ogg", ".m4a")):
            media_type = "audio"
        else:
            raise HTTPException(status_code=400, detail="Unsupported media type. Use image or audio files only.")

    try:
        service = RequestService(RequestRepository(db))
        request = service.upload_media(request_id, file, media_type)
        return MediaUploadResponse(
            request_id=request.request_id,
            status=request.status,
            image_url=request.image_url,
            audio_url=request.audio_url,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.get("/api/v1/requests/{request_id}", response_model=CitizenRequestResponse)
def get_request(request_id: str, db=Depends(get_db)):
    try:
        service = RequestService(RequestRepository(db))
        request = service.get_request(request_id)
        return request
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.get("/")
def root():
    return {"message": "Niti-Setu Citizen Ingestion Service"}
