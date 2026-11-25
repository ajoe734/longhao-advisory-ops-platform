"""M04 AML/CFT screening, restricted case and business clearance module.

The package deliberately exposes ports and a deterministic in-memory adapter so
the module can be developed before the shared PostgreSQL/FastAPI platform lands.
Production composition must supply the shared transaction, auth and provider
adapters; fake providers are refused by the production composition root.
"""

from .automation import CaseSummaryAutomationService, LocalCaseSummaryAssistant
from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import AmlCftService, ServiceResponse

__all__ = [
    "ActorContext",
    "AmlCftService",
    "CaseSummaryAutomationService",
    "DomainError",
    "InMemoryRepository",
    "LocalCaseSummaryAssistant",
    "ServiceResponse",
]
