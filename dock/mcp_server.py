"""Dock's tools as an MCP server (Section 5b). Run: python dock/mcp_server.py  (stdio)  or  python dock/mcp_server.py 8765  (HTTP)."""
import json, sqlite3, sys
from pathlib import Path
from fastmcp import FastMCP

HERE = Path(__file__).resolve().parent
db = sqlite3.connect(HERE / "dock.sqlite", check_same_thread=False)
OUTBOX = HERE / "outbox.jsonl"
mcp = FastMCP("dock")


@mcp.tool(annotations={"readOnlyHint": True})
def get_shipment(shipment_id: str) -> str:
    """Look up a shipment (like HF-26001): route, status, consignee and tracking events."""
    s = db.execute("SELECT s.id, s.status, c.company, c.email FROM shipments s "
                   "JOIN customers c ON c.id = s.consignee_id "
                   "WHERE s.id = ?", (shipment_id,)).fetchone()
    if s is None:
        return f"No shipment {shipment_id}"
    events = db.execute("SELECT at, event, note FROM tracking_events "
                        "WHERE shipment_id = ? ORDER BY at", (shipment_id,))
    return json.dumps({"id": s[0], "status": s[1], "consignee": s[2],
                       "consignee_email": s[3],
                       "events": [list(e) for e in events.fetchall()]})


@mcp.tool(annotations={"readOnlyHint": True})
def list_customers() -> str:
    """List all Harbor Freight customers with their emails."""
    return json.dumps(db.execute("SELECT company, email FROM customers").fetchall())


@mcp.tool(annotations={"destructiveHint": True})
def send_email(to: str, subject: str, body: str) -> str:
    """Send an email from Harbor Freight operations, to a Harbor Freight customer."""
    known = {row[0] for row in db.execute("SELECT email FROM customers")}
    if to not in known:
        return f"Refused by the Dock server: {to} is not a Harbor Freight customer"
    with OUTBOX.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"to": to, "subject": subject, "body": body}) + "\n")
    return f"Email sent to {to}"


if __name__ == "__main__":
    if len(sys.argv) > 1:
        mcp.run(transport="http", port=int(sys.argv[1]), show_banner=False)
    else:
        mcp.run(show_banner=False)
