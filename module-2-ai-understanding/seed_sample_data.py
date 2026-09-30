#!/usr/bin/env python3
"""Seed Module 2 with representative multilingual citizen complaints.

Run from the module directory:

    python seed_sample_data.py

The sample deliberately covers every supported language, both audio and image
submissions, and the severity range, so a judge can exercise the dashboard and
the analytics endpoints without inventing data. Everything here is synthetic
and free of any real citizen information.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.database import models_registry  # noqa: E402,F401  (registers models)
from app.database.connection import SessionLocal, init_db  # noqa: E402
from app.repositories.understanding_repository import (  # noqa: E402
    UnderstandingRepository,
)
from app.services.understanding_service import understand  # noqa: E402

# (request_id, text, source_channel)
TEXT_SAMPLES: list[tuple[str, str, str]] = [
    ("REQ-SEED-001", "No water supply for days in our colony, there is a broken pipeline near the temple", "MOBILE_APP"),
    ("REQ-SEED-002", "The transformer near the temple is burning and sparking, there is a danger of fire", "MOBILE_APP"),
    ("REQ-SEED-003", "Deep pothole caused an accident, children are crossing the road near the school", "MOBILE_APP"),
    ("REQ-SEED-004", "Garbage has not been collected for two weeks near the bus stop", "WEB_PORTAL"),
    ("REQ-SEED-005", "There is no doctor in the health centre and no medicine, my mother is ill", "IVR"),
    ("REQ-SEED-006", "Ambulance did not come when my father had a heart attack, three days no response", "IVR"),
    ("REQ-SEED-007", "Street light is not working on the road for two months, it is very dark at night", "MOBILE_APP"),
    ("REQ-SEED-008", "Sewage is overflowing on the street and mixing with the drinking water", "MOBILE_APP"),
    ("REQ-SEED-009", "No internet in our village for two months, mobile network is also down", "WEB_PORTAL"),
    ("REQ-SEED-010", "Bus is not coming for three days, the old people are struggling", "WEB_PORTAL"),
    ("REQ-SEED-011", "The school building wall is cracked and children are studying in the rain", "MOBILE_APP"),
    ("REQ-SEED-012", "Road is slightly damaged near my house pincode 560001", "MOBILE_APP"),
    # --- Tamil ---
    ("REQ-SEED-101", "கழிவு நீர் சாலையில் பாய்ந்து கொண்டிருக்கு ஆறு மாதங்கள்", "MOBILE_APP"),
    ("REQ-SEED-102", "தண்ணீர் இல்லை ஆறு நாட்களாக, குடிநீர் வரவில்லை", "IVR"),
    ("REQ-SEED-103", "பள்ளிக்கு அருகில் பெரிய பள்ளம் இருக்கு, பிள்ளைகள் அபாயத்தில் இருக்கு", "MOBILE_APP"),
    # --- Hindi ---
    ("REQ-SEED-201", "सेक्शन रोड पर बड़े गड्ढे हैं, बच्चे सड़क पार करते हैं", "MOBILE_APP"),
    ("REQ-SEED-202", "गंदा पानी नाली में जा रहा है, पिछले दो सप्ताह से", "WEB_PORTAL"),
    ("REQ-SEED-203", "बिजली नहीं आ रही, ट्रांसफार्मर जल रहा है", "IVR"),
    # --- Telugu ---
    ("REQ-SEED-301", "రోడ్డులో పెద్ద గుంతలు ఉన్నాయి, పిల్లలు పాఠశాలకు వెళ్తున్నారు", "MOBILE_APP"),
    ("REQ-SEED-302", "చెరువు ఎప్పుడూ తీయించారు, రెండు నెలలుగా", "WEB_PORTAL"),
    ("REQ-SEED-303", "వార్డులో నీరు లేదు, పన్నె రోజులుగా", "MOBILE_APP"),
]

# Minimal, obviously-synthetic media so the audio/image stages are exercised.
FAKE_AUDIO = b"RIFF\x24\x00\x00\x00WAVEfmt \x10\x00\x00\x00\x01\x00\x01\x00" + b"\x00" * 64
FAKE_IMAGE = b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01" + b"\x00" * 128


def seed() -> int:
    init_db()
    session = SessionLocal()
    repo = UnderstandingRepository(session)
    created = 0
    try:
        for request_id, text, channel in TEXT_SAMPLES:
            result = understand(request_id=request_id, text=text)
            repo.save(result, source_channel=channel, processing_ms=result.__dict__.get("processing_ms", 0) or 0)
            created += 1

        result = understand(request_id="REQ-SEED-901", audio_bytes=FAKE_AUDIO)
        repo.save(result, source_channel="IVR", processing_ms=0)
        created += 1

        result = understand(
            request_id="REQ-SEED-902",
            text="Broken road near the bus stop",
            image_bytes=FAKE_IMAGE,
        )
        repo.save(result, source_channel="MOBILE_APP", processing_ms=0)
        created += 1
    finally:
        session.close()

    print(f"Seeded {created} understanding records.")
    return created


if __name__ == "__main__":
    seed()