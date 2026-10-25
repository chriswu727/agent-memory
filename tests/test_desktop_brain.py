import os
from concurrent.futures import ThreadPoolExecutor

import pytest
from dotenv import load_dotenv

from agent_memory.helpers import DomainError, Principal, uid
from agent_memory.service import MemoryService
from agent_memory.storage import Database

pytestmark = pytest.mark.integration


@pytest.fixture
def service():
    load_dotenv()
    dsn = os.environ.get("MEMORY_DATABASE_URL")
    if not dsn:
        pytest.skip("MEMORY_DATABASE_URL must point to the dedicated test database")
    return MemoryService(Database(dsn))


def test_ensure_brain_preserves_identity_and_original_name(service):
    principal, brain_id = Principal(uid(), uid()), uid()
    first = service.ensure_brain(principal, brain_id, "Desktop")
    repeated = service.ensure_brain(principal, brain_id, "Changed display name")
    assert first == repeated
    assert first["id"] == brain_id
    assert first["name"] == "Desktop"
    assert service.list_brains(principal) == [first]


def test_concurrent_initialization_creates_only_one_brain(service):
    with ThreadPoolExecutor(max_workers=4) as executor:
        for _ in range(20):
            principal, brain_id = Principal(uid(), uid()), uid()
            results = list(executor.map(lambda _: service.ensure_brain(principal, brain_id, "Desktop"), range(4)))
            assert all(result == results[0] for result in results)
            assert service.list_brains(principal) == [results[0]]


@pytest.mark.parametrize("same_tenant", [False, True])
def test_existing_foreign_brain_cannot_be_adopted(service, same_tenant):
    owner, brain_id = Principal(uid(), uid()), uid()
    first = service.ensure_brain(owner, brain_id, "Private")
    other = Principal(owner.tenant_id if same_tenant else uid(), uid())
    with pytest.raises(DomainError) as error:
        service.ensure_brain(other, brain_id, "Attempted takeover")
    assert error.value.code == "not_found_or_forbidden"
    assert service.list_brains(other) == []
    assert service.list_brains(owner) == [first]


def test_existing_create_brain_still_creates_distinct_brains(service):
    principal = Principal(uid(), uid())
    first = service.create_brain(principal, "Same name")
    second = service.create_brain(principal, "Same name")
    assert first["id"] != second["id"]
    assert len(service.list_brains(principal)) == 2


@pytest.mark.parametrize("brain_id,name", [("", "name"), (None, "name"), ("x" * 257, "name"),
                                          ("brain", ""), ("brain", "x" * 201), ("brain", None)])
def test_invalid_desktop_brain_identity_or_name_is_rejected(service, brain_id, name):
    principal = Principal(uid(), uid())
    with pytest.raises(DomainError) as error:
        service.ensure_brain(principal, brain_id, name)
    assert error.value.code == "validation_failed"
    assert service.list_brains(principal) == []
