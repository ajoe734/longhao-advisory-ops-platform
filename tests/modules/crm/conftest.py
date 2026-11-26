from __future__ import annotations

from pathlib import Path
import sys
from uuid import uuid4

import pytest

_API_SRC = Path(__file__).resolve().parents[3] / "apps" / "api" / "src"
_WORKER_SRC = Path(__file__).resolve().parents[3] / "workers" / "tasks"
for path in (_API_SRC, _WORKER_SRC):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from modules.crm.domain import ActorContext  # noqa: E402
from modules.crm.ports import FakeEvidencePort  # noqa: E402
from modules.crm.repository import InMemoryRepository  # noqa: E402
from modules.crm.service import CrmService  # noqa: E402
from crm.consumer import M10EventConsumer  # noqa: E402
from crm.tasks import LocalEventBus, OutboxPublisher, WorklistSweepWorker  # noqa: E402


def new_id() -> str:
    return str(uuid4())


@pytest.fixture
def tenant_id() -> str:
    return new_id()


@pytest.fixture
def evidence() -> FakeEvidencePort:
    return FakeEvidencePort()


@pytest.fixture
def repository() -> InMemoryRepository:
    return InMemoryRepository()


@pytest.fixture
def service(repository: InMemoryRepository, evidence: FakeEvidencePort) -> CrmService:
    return CrmService(repository, evidence, environment="test")


@pytest.fixture
def consumer(repository: InMemoryRepository, service: CrmService) -> M10EventConsumer:
    return M10EventConsumer(repository, service)


@pytest.fixture
def event_bus() -> LocalEventBus:
    return LocalEventBus()


@pytest.fixture
def outbox_publisher(repository: InMemoryRepository, event_bus: LocalEventBus) -> OutboxPublisher:
    return OutboxPublisher(repository, event_bus)


@pytest.fixture
def sweep_worker(service: CrmService) -> WorklistSweepWorker:
    return WorklistSweepWorker(service)


class Actors:
    """Deterministic per-test actor ids so assertions can name roles clearly."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        self.sales_id = new_id()
        self.sales_id_2 = new_id()
        self.supervisor_id = new_id()
        self.service_agent_id = new_id()
        self.marketing_id = new_id()

    def sales(self, actor_id: str | None = None) -> ActorContext:
        return ActorContext(tenant_id=self.tenant_id, actor_id=actor_id or self.sales_id, roles=frozenset({"sales"}))

    def sales_2(self) -> ActorContext:
        return ActorContext(tenant_id=self.tenant_id, actor_id=self.sales_id_2, roles=frozenset({"sales"}))

    def supervisor(self) -> ActorContext:
        return ActorContext(tenant_id=self.tenant_id, actor_id=self.supervisor_id, roles=frozenset({"sales_supervisor"}))

    def service_agent(self) -> ActorContext:
        return ActorContext(tenant_id=self.tenant_id, actor_id=self.service_agent_id, roles=frozenset({"service_agent"}))

    def marketing(self) -> ActorContext:
        return ActorContext(tenant_id=self.tenant_id, actor_id=self.marketing_id, roles=frozenset({"marketing"}))


@pytest.fixture
def actors(tenant_id: str) -> Actors:
    return Actors(tenant_id)


# ``make_envelope`` lives in ``envelope.py``, not here: a plain
# ``from conftest import make_envelope`` inside a test function is resolved
# against whichever sibling test package's ``conftest`` module last claimed
# the bare ``conftest`` name in ``sys.modules`` during this pytest session,
# which is not guaranteed to be this directory's own conftest once more than
# one ``tests/modules/*`` package is collected together.
