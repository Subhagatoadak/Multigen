# tests/test_capability_directory.py
import pytest
from datetime import datetime, timedelta
from typing import List, Optional

import orchestrator.services.capability_directory as cd
from orchestrator.models.capability import Capability
from orchestrator.services.agent_registry import AgentRegistryError
import orchestrator.services.mongo as mongo_mod

# --- Fake Mongo classes ---
class FakeCollection:
    def __init__(self):
        self.docs = []
    async def find_one(self, query):
        # simple match on equality for provided keys
        for doc in self.docs:
            if all(doc.get(k) == v for k, v in query.items()):
                return doc.copy()
        return None
    
    async def insert_one(self, doc):
        # simulate setting _id
        new = doc.copy()
        self.docs.append(new)
        class Res: pass
        return Res()
    
    def find(self,filter=None):
        # return an async iterator over docs
        async def gen():
            for doc in self.docs:
                yield doc.copy()
        return gen()

class FakeDB:
    def __init__(self):
        self.collections = {}
    def __getitem__(self, name):
        if name not in self.collections:
            self.collections[name] = FakeCollection()
        return self.collections[name]

class FakeClient:
    def __init__(self):
        self.dbs = {}
    def __getitem__(self, name):
        if name not in self.dbs:
            self.dbs[name] = FakeDB()
        return self.dbs[name]

@pytest.fixture(autouse=True)
def fake_mongo(monkeypatch):
    """Monkeypatch get_mongo_client to return a fake in-memory client"""
    fake_client = FakeClient()
    monkeypatch.setattr(mongo_mod, 'get_mongo_client', lambda: fake_client)
    return fake_client

@pytest.mark.asyncio
async def test_list_empty():
    caps = await cd.list_capabilities()
    assert caps == []

@pytest.mark.asyncio
async def test_register_and_get_and_list(fake_mongo):
    # create capability
    cap = Capability(name="TestAgent", version="1.0.0", description="desc")
    before = datetime.utcnow()
    reg = await cd.register_capability(cap)
    after = datetime.utcnow()
    # created_at set
    assert isinstance(reg.created_at, datetime)
    assert before <= reg.created_at <= after + timedelta(seconds=1)
    # get by name/version
    got = await cd.get_capability("TestAgent", "1.0.0")
    assert got.name == "TestAgent"
    assert got.version == "1.0.0"
    assert got.description == "desc"
    # list returns one
    caps = await cd.list_capabilities()
    assert len(caps) == 1
    assert caps[0].name == "TestAgent"

@pytest.mark.asyncio
async def test_register_conflict(fake_mongo):
    cap = Capability(name="A", version="v1")
    await cd.register_capability(cap)
    with pytest.raises(AgentRegistryError) as exc:
        await cd.register_capability(cap)
    assert "already registered" in str(exc.value)

@pytest.mark.asyncio
async def test_get_not_found(fake_mongo):
    with pytest.raises(AgentRegistryError) as exc:
        await cd.get_capability("NoExist")
    assert "not found" in str(exc.value)

@pytest.mark.asyncio
async def test_get_latest_without_version(fake_mongo):
    # Register two versions, then query without version returns any (first match)
    cap1 = Capability(name="B", version="1.0")
    cap2 = Capability(name="B", version="2.0")
    await cd.register_capability(cap1)
    await cd.register_capability(cap2)
    # Without version, get fetches first inserted
    got = await cd.get_capability("B")
    assert got.name == "B"
    assert got.version in ("1.0", "2.0")
