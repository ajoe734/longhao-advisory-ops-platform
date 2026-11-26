"""M03 online KYC, customer data and suitability module.

The package deliberately exposes ports and a deterministic in-memory adapter so
the module can be developed before the shared PostgreSQL/FastAPI platform lands.
Production composition must supply the shared transaction, auth and provider
adapters; fake providers are refused by the production composition root.
"""

from .automation import LocalOcrAssistant, OcrAutomationService
from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import KycSuitabilityService, ServiceResponse

__all__ = [
    "ActorContext",
    "DomainError",
    "InMemoryRepository",
    "KycSuitabilityService",
    "LocalOcrAssistant",
    "OcrAutomationService",
    "ServiceResponse",
]
