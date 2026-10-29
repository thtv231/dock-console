"""A live handoff creates agents on the visitor's Mistral account, so it must never leave one behind unreported.

Mistral is stubbed: the client built from the visitor's key is a fake that records every agent it creates and deletes,
and fails or hangs at a chosen step.
"""
import asyncio
import itertools
import threading

import httpx2
import pytest
from fastapi.testclient import TestClient
from mistralai.client.errors import SDKError

FAKE_KEY = "dock-test-key-0000000000000000"   # not a real key
HANDOFF = {"mail_id": "M03", "rule": "v2"}
HANG_S = 5


class FakeMistral:
    """Just enough of client.beta for a handoff: agents create/update/delete, conversations.run_async."""

    def __init__(self, fail_at=None, hang=()):
        self.fail_at, self.hang = fail_at, set(hang)
        self.created, self.deleted, self.creates = [], [], 0
        self.ids = (f"ag_fake{n:04d}" for n in itertools.count(1))
        self.released = threading.Event()
        agents = type("Agents", (), {})()
        for name in ("create", "update", "delete"):
            setattr(agents, name, getattr(self, "_" + name))
            setattr(agents, name + "_async", self._async(getattr(self, "_" + name), name))
        conversations = type("Conversations", (), {})()
        conversations.run_async = self._async(self._run, "run")
        self.beta = type("Beta", (), {})()
        self.beta.agents, self.beta.conversations = agents, conversations

    def _step(self, step):
        if step in self.hang:
            self.released.wait(HANG_S)
        if self.fail_at == step:
            raise SDKError("stubbed failure", httpx2.Response(500, request=httpx2.Request("POST", "https://stub")))

    def _async(self, fn, step):
        async def call(*args, **kwargs):
            if step in self.hang:
                for _ in range(HANG_S * 20):
                    if self.released.is_set():
                        break
                    await asyncio.sleep(0.05)
            return fn(*args, **kwargs)
        return call

    def _create(self, **kwargs):
        self.creates += 1
        self._step(f"create{self.creates}")
        agent = type("Agent", (), {"id": next(self.ids), "name": kwargs.get("name"), "handoffs": []})()
        self.created.append(agent.id)
        return agent

    def _update(self, agent_id, **kwargs):
        self._step("update")
        return type("Agent", (), {"id": agent_id, "name": None, "handoffs": kwargs.get("handoffs") or []})()

    def _delete(self, agent_id, **kwargs):
        self._step("delete")
        self.deleted.append(agent_id)

    def _run(self, **kwargs):
        self._step("run")
        raise AssertionError("the stub never finishes a run: every test fails or hangs before this")

    def __exit__(self, *exc):
        pass

    async def __aexit__(self, *exc):
        pass


@pytest.fixture
def stub(monkeypatch):
    """Install a FakeMistral as the client every request builds from its key, and shorten the handoff deadlines."""
    made = []

    def install(**kwargs):
        fake = FakeMistral(**kwargs)
        made.append(fake)
        monkeypatch.setattr("dock._client._build", lambda key: fake)
        return fake

    monkeypatch.setenv("DOCK_LIVE_PER_MIN", "0")
    monkeypatch.setattr("api.routes.agents.DEADLINE_S", 0.3)
    monkeypatch.setattr("api.routes.agents.CLEANUP_S", 0.3)
    yield install
    for fake in made:
        fake.released.set()


@pytest.fixture
def http():
    from api.index import app
    return TestClient(app, raise_server_exceptions=False)


def post(http):
    return http.post("/api/agents/handoff", json=HANDOFF, headers={"X-Mistral-Key": FAKE_KEY})


@pytest.mark.parametrize("fail_at", ["create2", "create3", "update", "run"])
def test_failure_half_way_still_deletes_every_agent(stub, http, fail_at):
    fake = stub(fail_at=fail_at)
    r = post(http)
    assert r.status_code != 200, r.text
    assert fake.created, f"failing at {fail_at} created nothing, so cleanup was not tested"
    assert sorted(fake.deleted) == sorted(fake.created), f"created {fake.created}, deleted {fake.deleted}"


def test_timeout_reports_the_agents_it_could_not_delete(stub, http):
    fake = stub(hang={"run", "delete"})
    r = post(http)
    assert r.status_code == 504 and r.json()["error"] == "timeout", r.text
    assert len(fake.created) == 3, fake.created
    assert sorted(r.json()["agents_left"]) == sorted(fake.created), r.json()


def test_timeout_still_deletes_when_mistral_answers(stub, http):
    fake = stub(hang={"run"})
    r = post(http)
    assert r.status_code == 504 and r.json()["error"] == "timeout", r.text
    assert sorted(fake.deleted) == sorted(fake.created) and r.json()["agents_left"] == [], r.json()
