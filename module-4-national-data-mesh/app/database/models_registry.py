"""Model registry.

Importing every model module here guarantees ``Base.metadata`` is populated
before ``create_all`` runs, so a new table cannot be silently missed by a
developer who forgets an import somewhere else. ``connection.init_db`` imports
this module for exactly that reason.
"""

from app.models.mesh_tables import (
    Base,
    CensusIndicator,
    CivicRecordLineage,
    CitizenDemand,
    DataProduct,
    GapRecord,
    InfrastructureAsset,
    InvestmentProject,
    Ward,
)

__all__ = [
    "Base",
    "CensusIndicator",
    "CivicRecordLineage",
    "CitizenDemand",
    "DataProduct",
    "GapRecord",
    "InfrastructureAsset",
    "InvestmentProject",
    "Ward",
]