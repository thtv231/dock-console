"""MCP (S5b) at /api/mcp: an outside client, over HTTP, sees Dock's three tools and gets the server's own refusal for
a recipient who is not a customer. The simulated outbox is written under DOCK_OUTBOX (a temp dir here, /tmp on
Vercel, where the repo is read-only), never inside the repo."""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from fastmcp import Client

ROOT = Path(__file__).resolve().parent.parent
TOOLS = {"get_shipment", "list_customers", "send_email"}
CUSTOMER = "import@nordlicht.example.com"
AUDIT = "audit@harbour-review.example.net"   # the address the HF-26038 tracking note tries to get mailed to
REFUSAL = f"Refused by the Dock server: {AUDIT} is not a Harbor Freight customer"   # s05b
NOT_REPO = {".git", ".venv", "node_modules", ".next", "__pycache__", ".pytest_cache"}


@pytest.fixture(scope="module")
def mcp_url():
    """The API under uvicorn, as Vercel serves it: lifespan on, so the MCP session manager runs."""
    import uvicorn
    from api.index import app
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=0, lifespan="on", log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 30
    while not server.started:
        assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
        time.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    yield f"http://127.0.0.1:{port}/api/mcp"
    server.should_exit = True
    thread.join(10)


def mcp(url, use):
    async def go():
        async with Client(url) as c:
            return await use(c)
    return asyncio.run(go())


def send(url, to):
    result = mcp(url, lambda c: c.call_tool("send_email", {"to": to, "subject": "HF-26038 update",
                                                            "body": f"Test {time.time_ns()}"}))
    return result.content[0].text


def snapshot():
    files = {}
    for dirpath, dirnames, filenames in os.walk(ROOT):
        dirnames[:] = [d for d in dirnames if d not in NOT_REPO]
        for name in filenames:
            p = Path(dirpath) / name
            files[p] = p.stat().st_mtime_ns
    return files


@pytest.fixture
def repo_writes():
    """Files a test created or changed inside the repo. New files are removed afterwards, so the tree stays clean."""
    before = snapshot()
    yield lambda: [p.relative_to(ROOT).as_posix() for p, t in snapshot().items() if before.get(p) != t]
    for p in snapshot():
        if p not in before:
            p.unlink()


def outbox_lines():
    out = Path(os.environ["DOCK_OUTBOX"])
    return [json.loads(line) for f in sorted(out.rglob("*")) if f.is_file()
            for line in f.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_the_outbox_dir_is_outside_the_repo():
    """conftest points DOCK_OUTBOX at a temp dir, so nothing below can pass by writing into the repo."""
    assert not Path(os.environ["DOCK_OUTBOX"]).resolve().is_relative_to(ROOT)


def test_a_client_lists_exactly_the_three_tools(mcp_url):
    tools = mcp(mcp_url, lambda c: c.list_tools())
    assert {t.name for t in tools} == TOOLS, [t.name for t in tools]


def test_send_email_is_marked_as_writing_and_the_others_as_reading(mcp_url):
    tools = {t.name: t.annotations for t in mcp(mcp_url, lambda c: c.list_tools())}
    assert tools["send_email"] and tools["send_email"].destructive_hint is True, tools["send_email"]
    assert not tools["send_email"].read_only_hint, tools["send_email"]
    for name in TOOLS - {"send_email"}:
        assert tools[name] and tools[name].read_only_hint is True, (name, tools[name])


def test_send_email_to_audit_is_refused(mcp_url, repo_writes):
    before = outbox_lines()
    assert send(mcp_url, AUDIT) == REFUSAL
    assert outbox_lines() == before, "a refused email reached the outbox"
    assert repo_writes() == []


def test_a_customer_email_is_written_under_dock_outbox(mcp_url, repo_writes):
    before = outbox_lines()
    assert send(mcp_url, CUSTOMER) == f"Email sent to {CUSTOMER}"
    added = outbox_lines()[len(before):]
    assert [m["to"] for m in added] == [CUSTOMER], added
    assert repo_writes() == [], f"the send wrote inside the repo: {repo_writes()}"


def test_with_no_dock_outbox_the_outbox_is_still_outside_the_repo(repo_writes):
    """A fresh process with DOCK_OUTBOX unset sends one email in process: nothing in the repo may change."""
    env = {k: v for k, v in os.environ.items() if k != "DOCK_OUTBOX"}
    code = ("import asyncio; from fastmcp import Client; from dock.mcp_server import mcp\n"
            "async def go():\n"
            "    async with Client(mcp) as c:\n"
            f"        r = await c.call_tool('send_email', {{'to': {CUSTOMER!r}, 'subject': 's', 'body': 'b'}})\n"
            "        print(r.content[0].text)\n"
            "asyncio.run(go())")
    r = subprocess.run([sys.executable, "-c", code], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-500:]
    assert r.stdout.strip() == f"Email sent to {CUSTOMER}", r.stdout
    assert repo_writes() == [], f"with DOCK_OUTBOX unset, the send wrote inside the repo: {repo_writes()}"


# ---------- The console's tool tester: /api/ops/mcp/tools and /api/ops/mcp/call, the same server in process ----------

def call_tester(client, tool, args, **extra):
    return client.post("/api/ops/mcp/call", json={"tool": tool, "args": args, **extra})


def test_the_tester_lists_the_three_tools_with_their_hints_and_arguments(client):
    r = client.get("/api/ops/mcp/tools")
    assert r.status_code == 200, r.text[:200]
    tools = {t["name"]: t for t in r.json()["tools"]}
    assert set(tools) == TOOLS
    assert (tools["send_email"]["read_only"], tools["send_email"]["destructive"]) == (False, True)
    for name in TOOLS - {"send_email"}:
        assert (tools[name]["read_only"], tools[name]["destructive"]) == (True, False), name
    assert set(tools["send_email"]["input_schema"]["properties"]) == {"to", "subject", "body"}
    assert set(tools["get_shipment"]["input_schema"]["properties"]) == {"shipment_id"}


def test_the_tester_reads_a_shipment(client):
    r = call_tester(client, "get_shipment", {"shipment_id": "HF-26038"})
    assert r.status_code == 200, r.text[:200]
    got = r.json()
    assert not got["is_error"] and json.loads(got["text"])["consignee_email"] == CUSTOMER


def test_the_tester_will_not_send_without_approval(client, repo_writes):
    """CLAUDE.md: a write needs explicit approval in the UI, so the server asks for it too."""
    before = outbox_lines()
    r = call_tester(client, "send_email", {"to": CUSTOMER, "subject": "s", "body": "b"})
    assert r.status_code == 400 and r.json()["error"] == "needs_approval", r.text[:200]
    assert outbox_lines() == before and repo_writes() == []


def test_the_tester_gets_the_refusal_for_audit_even_when_approved(client, repo_writes):
    before = outbox_lines()
    r = call_tester(client, "send_email", {"to": AUDIT, "subject": "s", "body": "b"}, approved=True)
    assert r.status_code == 200, r.text[:200]
    assert r.json()["text"] == REFUSAL
    assert outbox_lines() == before and repo_writes() == []


def test_an_approved_send_to_a_customer_lands_under_dock_outbox(client, repo_writes):
    before = outbox_lines()
    r = call_tester(client, "send_email", {"to": CUSTOMER, "subject": "s", "body": f"b {time.time_ns()}"},
                    approved=True)
    assert r.status_code == 200 and r.json()["text"] == f"Email sent to {CUSTOMER}", r.text[:200]
    assert [m["to"] for m in outbox_lines()[len(before):]] == [CUSTOMER]
    assert repo_writes() == []


def test_the_tester_refuses_an_unknown_tool(client):
    r = call_tester(client, "delete_everything", {})
    assert r.status_code == 400 and r.json()["error"] == "unknown_tool", r.text[:200]


def test_missing_arguments_come_back_as_a_tool_error(client):
    before = outbox_lines()
    r = call_tester(client, "send_email", {}, approved=True)
    assert r.status_code == 200 and r.json()["is_error"], r.text[:200]
    assert outbox_lines() == before
