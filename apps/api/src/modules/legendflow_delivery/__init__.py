"""M09 LegendFlow member feed and multi-channel delivery module.

The package deliberately exposes ports and deterministic in-memory adapters
so the module can be developed before the shared PostgreSQL/FastAPI platform
lands. Production composition must supply the shared transaction, auth and
provider adapters; fake providers are refused by the production composition
root.
"""

from .automation import DeliveryExceptionAutomationService, LocalExceptionSummaryAssistant
from .domain import ActorContext, DomainError
from .repository import InMemoryRepository
from .service import LegendflowDeliveryService, ServiceResponse

__all__ = [
    "ActorContext",
    "DeliveryExceptionAutomationService",
    "DomainError",
    "InMemoryRepository",
    "LegendflowDeliveryService",
    "LocalExceptionSummaryAssistant",
    "ServiceResponse",
]
