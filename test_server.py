"""Talk to the MCP server the way an agent would, without Claude. Use it to check the server works.

Usage: python test_server.py
"""

import json
import subprocess
import sys
from pathlib import Path

SERVER = Path(__file__).parent / "server" / "payflow_data_server.py"


class Client:
    def __init__(self):
        self.proc = subprocess.Popen(
            [sys.executable, str(SERVER)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
        )
        self.next_id = 1

    def send(self, method, params=None, notify=False):
        msg = {"jsonrpc": "2.0", "method": method, "params": params or {}}
        if not notify:
            msg["id"] = self.next_id
            self.next_id += 1
        self.proc.stdin.write(json.dumps(msg) + "\n")
        self.proc.stdin.flush()
        return None if notify else json.loads(self.proc.stdout.readline())

    def call(self, tool, **arguments):
        result = self.send("tools/call", {"name": tool, "arguments": arguments})["result"]
        return result["isError"], result["content"][0]["text"]

    def close(self):
        self.proc.stdin.close()
        self.proc.wait(timeout=5)


def check(label, condition):
    print(("PASS " if condition else "FAIL ") + label)
    return condition


def main() -> int:
    c = Client()
    results = []
    init = c.send("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}})
    results.append(check("initialize returns server info", init["result"]["serverInfo"]["name"] == "payflow-data"))
    c.send("notifications/initialized", notify=True)

    tools = [t["name"] for t in c.send("tools/list")["result"]["tools"]]
    results.append(check(f"tools/list -> {tools}", tools == ["search_transfers", "get_transfer", "transfer_summary"]))

    err, text = c.call("search_transfers", status="failed", limit=3)
    data = json.loads(text)
    results.append(check(f"search failed transfers: {data['total_matches']} matches, {data['returned']} returned",
                         not err and data["returned"] <= 3 and all(r["status"] == "failed" for r in data["records"])))
    first = data["records"][0]
    results.append(check(f"masking: account {first['account_number']}, phone {first['phone']}, email {first['email']}",
                         first["account_number"].startswith("X") and "XXXXX" in first["phone"] and "***" in first["email"]))

    err, text = c.call("get_transfer", record_id="REC-00042")
    results.append(check("get_transfer REC-00042", not err and json.loads(text)["record_id"] == "REC-00042"))

    err, text = c.call("get_transfer", record_id="REC-99999")
    results.append(check(f"unknown id is a tool error: '{text}'", err))

    err, text = c.call("transfer_summary", month="2026-03")
    s = json.loads(text)
    results.append(check(f"summary March 2026: {s['transfers']} transfers, INR {s['total_inr']}", not err and s["transfers"] > 0))

    bad = c.send("tools/call", {"name": "drop_table", "arguments": {}})
    results.append(check("unknown tool rejected", "error" in bad))

    c.close()
    print(f"\n{sum(results)}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
