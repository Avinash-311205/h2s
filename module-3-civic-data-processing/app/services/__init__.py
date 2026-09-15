"""Processing services for Module 3.

Each service owns exactly one responsibility, mirroring the pipeline:

    cleaning -> normalization -> geocoding -> deduplication -> validation -> quality
"""

from app.services.cleaning_service import CleaningService
from app.services.deduplication_service import DeduplicationService
from app.services.geocoding_service import GeocodingService, get_geocoder
from app.services.normalization_service import NormalizationService
from app.services.quality_service import QualityService
from app.services.validation_service import ValidationService

__all__ = [
    "CleaningService",
    "DeduplicationService",
    "GeocodingService",
    "NormalizationService",
    "QualityService",
    "ValidationService",
    "get_geocoder",
]
