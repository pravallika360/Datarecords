"""The same agent, run from Python with the Claude Agent SDK (the route your Agent Runner will use).

Install:  pip install claude-agent-sdk      (also needs Claude Code installed and logged in, or ANTHROPIC_API_KEY set)
Run:      python agent_sdk_example.py "How many transfers failed in March 2026?"

Check option names against the Agent SDK docs for your installed version.
"""

import asyncio
import sys
from pathlib import Path

from claude_agent_sdk import AssistantMessage, ClaudeAgentOptions, ResultMessage, ToolUseBlock, query

HERE = Path(__file__).parent
ROLE = (HERE / ".claude" / "agents" / "payflow-data-analyst.md").read_text().split("---", 2)[2].strip()

TOOLS = [
    "mcp__payflow-data__search_transfers",
    "mcp__payflow-data__get_transfer",
    "mcp__payflow-data__transfer_summary",
]


async def ask(question: str) -> None:
    options = ClaudeAgentOptions(
        cwd=str(HERE),
        system_prompt=ROLE,
        mcp_servers={
            "payflow-data": {
                "type": "stdio",
                "command": sys.executable,
                "args": [str(HERE / "server" / "payflow_data_server.py")],
            }
        },
        allowed_tools=TOOLS,     # these run without a permission prompt
        tools=[],                # no built-in tools (no Bash, Read, Edit): MCP only
        max_turns=15,
    )
    async for message in query(prompt=question, options=options):
        if isinstance(message, AssistantMessage):
            for block in message.content:
                if isinstance(block, ToolUseBlock):
                    print(f"[tool] {block.name} {block.input}")
        elif isinstance(message, ResultMessage):
            print("\n" + (message.result or f"Run ended: {message.subtype}"))


if __name__ == "__main__":
    asyncio.run(ask(" ".join(sys.argv[1:]) or "Summarise transfers by status for March 2026."))
