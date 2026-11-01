"""PayFlow Data MCP server: read-only access to synthetic transfer records.

Standard library only (Python 3.10+). Speaks MCP over stdio: newline-delimited
JSON-RPC 2.0 on stdin/stdout. Logs go to stderr, never stdout.

Tools
  search_transfers   filter by status, amount range, date range
  get_transfer       one record by id
  transfer_summary   counts and totals by status, optionally for one month

Sensitive fields are masked before they leave the server (account number,
phone, email), so the agent never sees them in full.

Run it directly to check it starts:   python server/payflow_data_server.py
(it then waits for JSON-RPC on stdin; Ctrl+C to stop)
"""

import json
import os
import sys
from collections import defaultdict
from pathlib import Path

SERVER_NAME = "payflow-data"
SERVER_VERSION = "1.0.0"
SUPPORTED_PROTOCOLS = ["2025-06-18", "2025-03-26", "2024-11-05"]
DATA_FILE = Path(os.environ.get("PAYFLOW_DATA_FILE", Path(__file__).parent / "data" / "transfers.json"))
STATUSES = ["completed", "partially_refunded", "refunded", "failed"]
MAX_LIMIT = 100


def log(msg: str) -> None:
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)


def load_records() -> list[dict]:
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def mask(record: dict) -> dict:
    """Return a copy that is safe to hand to an agent."""
    r = dict(record)
    acct = r.get("account_number", "")
    r["account_number"] = "X" * max(len(acct) - 4, 0) + acct[-4:]
    phone = r.get("phone", "")
    r["phone"] = phone[:3] + " XXXXX XX" + phone[-3:] if len(phone) > 7 else "XXXX"
    user, _, domain = r.get("email", "").partition("@")
    r["email"] = (user[:2] + "***@" + domain) if domain else "***"
    r["amount_inr"] = f"{r['amount_paise'] / 100:,.2f}"
    return r


# ---------------------------------------------------------------- tools

TOOLS = [
    {
        "name": "search_transfers",
        "description": (
            "Search PayFlow transfer records. All filters are optional and combine with AND. "
            "Amounts are in paise (100 paise = 1 INR). Dates are YYYY-MM-DD, inclusive. "
            "Returns newest first, masked."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "status": {"type": "string", "enum": STATUSES},
                "min_amount_paise": {"type": "integer", "minimum": 0},
                "max_amount_paise": {"type": "integer", "minimum": 0},
                "from_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                "to_date": {"type": "string", "pattern": "^\\d{4}-\\d{2}-\\d{2}$"},
                "limit": {"type": "integer", "minimum": 1, "maximum": MAX_LIMIT, "default": 20},
            },
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "get_transfer",
        "description": "Get one transfer record by its record_id, e.g. REC-00042. Returned masked.",
        "inputSchema": {
            "type": "object",
            "properties": {"record_id": {"type": "string", "pattern": "^REC-\\d{5}$"}},
            "required": ["record_id"],
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
    {
        "name": "transfer_summary",
        "description": (
            "Count and total amount of transfers grouped by status. "
            "Optional month filter as YYYY-MM. Use this for totals instead of adding up search results."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {"month": {"type": "string", "pattern": "^\\d{4}-\\d{2}$"}},
            "additionalProperties": False,
        },
        "annotations": {"readOnlyHint": True},
    },
]


class ToolError(Exception):
    """An error the agent should read and react to (bad arguments, not found)."""


def search_transfers(args: dict) -> dict:
    status = args.get("status")
    if status is not None and status not in STATUSES:
        raise ToolError(f"status must be one of {STATUSES}")
    lo = args.get("min_amount_paise", 0)
    hi = args.get("max_amount_paise")
    from_d, to_d = args.get("from_date"), args.get("to_date")
    limit = args.get("limit", 20)
    if not isinstance(limit, int) or not 1 <= limit <= MAX_LIMIT:
        raise ToolError(f"limit must be an integer from 1 to {MAX_LIMIT}")

    hits = []
    for r in load_records():
        day = r["created_at"][:10]
        if status and r["status"] != status:
            continue
        if r["amount_paise"] < lo or (hi is not None and r["amount_paise"] > hi):
            continue
        if (from_d and day < from_d) or (to_d and day > to_d):
            continue
        hits.append(r)
    hits.sort(key=lambda r: r["created_at"], reverse=True)
    return {"total_matches": len(hits), "returned": min(len(hits), limit), "records": [mask(r) for r in hits[:limit]]}


def get_transfer(args: dict) -> dict:
    rid = args.get("record_id")
    if not rid:
        raise ToolError("record_id is required")
    for r in load_records():
        if r["record_id"] == rid:
            return mask(r)
    raise ToolError(f"No transfer with record_id {rid}")


def transfer_summary(args: dict) -> dict:
    month = args.get("month")
    groups = defaultdict(lambda: {"count": 0, "total_paise": 0})
    for r in load_records():
        if month and not r["created_at"].startswith(month):
            continue
        g = groups[r["status"]]
        g["count"] += 1
        g["total_paise"] += r["amount_paise"]
    by_status = {s: {**g, "total_inr": f"{g['total_paise'] / 100:,.2f}"} for s, g in sorted(groups.items())}
    return {
        "month": month or "all",
        "transfers": sum(g["count"] for g in groups.values()),
        "total_inr": f"{sum(g['total_paise'] for g in groups.values()) / 100:,.2f}",
        "by_status": by_status,
    }


HANDLERS = {"search_transfers": search_transfers, "get_transfer": get_transfer, "transfer_summary": transfer_summary}


# ---------------------------------------------------------------- JSON-RPC

def handle(msg: dict) -> dict | None:
    """Handle one JSON-RPC message. Returns a response, or None for notifications."""
    method, msg_id, params = msg.get("method"), msg.get("id"), msg.get("params") or {}

    if msg_id is None:  # notification, e.g. notifications/initialized
        return None

    def ok(result):
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code, message):
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        asked = params.get("protocolVersion")
        version = asked if asked in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return ok({
            "protocolVersion": version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            "instructions": "Read-only access to synthetic PayFlow transfer records. Amounts are paise.",
        })
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        if name not in HANDLERS:
            return err(-32602, f"Unknown tool: {name}")
        try:
            data = HANDLERS[name](args)
            return ok({"content": [{"type": "text", "text": json.dumps(data, indent=2)}], "isError": False})
        except ToolError as exc:  # the agent reads this and can retry with better arguments
            return ok({"content": [{"type": "text", "text": str(exc)}], "isError": True})
        except Exception as exc:  # unexpected: log details, give the agent a short message
            log(f"tool {name} failed: {exc!r}")
            return ok({"content": [{"type": "text", "text": f"{name} failed on the server"}], "isError": True})
    return err(-32601, f"Method not found: {method}")


def main() -> None:
    log(f"starting, data file {DATA_FILE}")
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            resp = handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
