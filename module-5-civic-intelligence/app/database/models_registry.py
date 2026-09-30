"""Model registry.

Importing every model here guarantees ``Base.metadata`` is populated before
``create_all`` runs, so a new table cannot be silently missed.
"""

from app.models.intelligence_tables import (
    Base,
    DemandWindow,
    EmergingRisk,
    GapSnapshot,
    Hotspot,
    IntelligenceRun,
    ProjectSnapshot,
    Trend,
    WardLocation,
)

__all__ = [
    "Base",
    "DemandWindow",
    "EmergingRisk",
    "GapSnapshot",
    "Hotspot",
    "IntelligenceRun",
    "ProjectSnapshot",
    "Trend",
    "WardLocation",
]