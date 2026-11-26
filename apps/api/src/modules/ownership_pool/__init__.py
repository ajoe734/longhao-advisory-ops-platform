"""M11 maintenance ownership and company pool module.

The package deliberately exposes ports and a deterministic in-memory adapter so
the module can be developed before the shared PostgreSQL platform lands.
Production composition must supply real evidence/restriction adapters; fake
providers are refused by the production composition root.
"""

from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import OwnershipPoolService, ServiceResponse

__all__ = [
    "ActorContext",
    "DomainError",
    "InMemoryRepository",
    "OwnershipPoolService",
    "ServiceResponse",
]
