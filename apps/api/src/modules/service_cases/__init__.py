"""M13 cross-channel service case, complaint and inter-department module.

The package deliberately exposes ports and a deterministic in-memory adapter so
the module can be developed before the shared PostgreSQL/FastAPI platform lands.
Production composition must supply the shared transaction, provider and
drafting-assistant adapters; fake providers are refused by the production
composition root.
"""

from .automation import DraftingAssistantPort, LocalDraftingAssistant
from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import ServiceCaseService, ServiceResponse

__all__ = [
    "ActorContext",
    "DomainError",
    "DraftingAssistantPort",
    "InMemoryRepository",
    "LocalDraftingAssistant",
    "ServiceCaseService",
    "ServiceResponse",
]
