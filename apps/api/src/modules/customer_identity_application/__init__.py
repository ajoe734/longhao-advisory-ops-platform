"""M01 customer identity and application module.

The package deliberately exposes ports and a deterministic in-memory adapter so
the module can be developed before the shared PostgreSQL/FastAPI platform lands.
Production composition must supply the shared transaction, auth and provider
adapters; fake providers are refused by the production composition root.
"""

from .domain import ActorContext, DomainError
from .automation import DuplicateAutomationService, LocalDuplicateAssistant
from .repository import InMemoryRepository
from .service import CustomerIdentityApplicationService, ServiceResponse

__all__ = [
    "ActorContext",
    "CustomerIdentityApplicationService",
    "DuplicateAutomationService",
    "DomainError",
    "InMemoryRepository",
    "LocalDuplicateAssistant",
    "ServiceResponse",
]
