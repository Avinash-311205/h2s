"""
database.py -- Read access to the shared citizen_requests SQLite database.

Uses Python's stdlib sqlite3 with row_factory = sqlite3.Row so the code has
zero third-party dependencies for DB access and runs anywhere. This module is
read-only by design: cluster/score logic must never mutate citizen data.
"""

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from clustering import CitizenRequest


class RequestsDatabase:
    """Thin, read-only wrapper around the citizen_requests SQLite DB."""

    def __init__(self, db_path: str):
        """Point the reader at a SQLite file (e.g. ../citizen_requests.db)."""
        self.db_path: Path = Path(db_path)

    def _connect(self) -> sqlite3.Connection:
        """Open a read connection (row_factory -> dict-like access)."""
        if not self.db_path.exists():
            raise FileNotFoundError(
                f"Database not found at {self.db_path}. "
                "Run module-1-citizen-ingestion first to create it."
            )
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        return conn

    def fetch_all_requests(self) -> list:
        """Load every citizen request from the `requests` table.

        Returns a list of CitizenRequest dataclasses (safe for the rest of
        the pipeline; raw rows are never exposed beyond this layer).

        Raises:
            sqlite3.Error: if the table or a required column is missing.
        """
        query = """
            SELECT id, category, description, latitude, longitude,
                   severity, language, status, created_at
            FROM requests
        """
        conn = self._connect()
        try:
            rows = conn.execute(query).fetchall()
        except sqlite3.OperationalError as e:
            raise RuntimeError(
                "Could not read `requests` table -- is citizen_requests.db "
                f"fully initialised? Original error: {e}"
            ) from e
        finally:
            conn.close()

        requests = []
        for row in rows:
            created_at = row["created_at"]
            if isinstance(created_at, str):
                # DB may store ISO strings; normalise to timezone-aware datetime.
                if created_at.endswith("Z"):
                    created_at = created_at.replace("Z", "+00:00")
                created_at = datetime.fromisoformat(created_at)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)

            requests.append(
                CitizenRequest(
                    id=row["id"],
                    category=row["category"],
                    description=row["description"],
                    latitude=float(row["latitude"]),
                    longitude=float(row["longitude"]),
                    severity=float(row["severity"]),
                    language=row["language"],
                    status=row["status"],
                    created_at=created_at,
                )
            )
        return requests