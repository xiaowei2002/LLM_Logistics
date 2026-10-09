"""
MCP 连通性 + 工具调用 demo。

验证三件事：
1. 能否连上 mysql-mcp-server（SSE）并拿到工具列表；
2. 工具列表里有哪些可用的函数；
3. 模型能否在回答时实际调用这些工具（打印中间 tool_calls / 工具返回）。

前置条件：已启动 mysql-mcp-server（见 backend/README.md）。
运行：uv run python backend/scripts/demo_sql_agent.py
"""

import asyncio
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))

from langchain.agents import create_agent  # noqa: E402
from langchain_core.messages import AIMessage, ToolMessage  # noqa: E402

from core.agents.sql_agent import SQLAgent  # noqa: E402


def render_tool_calls(message: AIMessage) -> str:
    lines = []
    for call in getattr(message, "tool_calls", []) or []:
        lines.append(f"  → 调用工具 [{call.get('name')}] 参数: {call.get('args')}")
    return "\n".join(lines)


async def main() -> None:
    agent = SQLAgent()

    print("[1/3] 连接 mysql-mcp-server，加载工具...")
    try:
        tools = await agent.mcp.get_tools()
    except Exception as exc:  # noqa: BLE001
        print(f"  ✗ 连接失败：{exc!r}")
        print("  请确认已启动 mysql-mcp-server：uvx --from mysql-mcp-server mysql_mcp_server")
        return

    if not tools:
        print("  ✗ 没有拿到任何工具，检查 MYSQL_MCP_URL 与 MCP 服务状态")
        return

    print(f"  ✓ 拿到 {len(tools)} 个工具：")
    for tool in tools:
        desc = (getattr(tool, "description", "") or "").strip().splitlines()
        head = desc[0] if desc else ""
        print(f"    - {tool.name}: {head}")

    print("\n[2/3] 构建 Agent（模型 + 工具）...")
    _ = await agent.ensure_agent()
    print("  ✓ Agent 就绪，模型 =", agent.client.settings.model)

    question = "列出数据库里所有的表"
    print(f"\n[3/3] 提问：{question}\n")

    result = await agent.agent.ainvoke(
        {"messages": [{"role": "user", "content": question}]}
    )
    messages = result.get("messages", [])

    for msg in messages:
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            print("[模型意图] 决定调用工具：")
            print(render_tool_calls(msg))
        elif isinstance(msg, ToolMessage):
            content = str(getattr(msg, "content", ""))
            preview = content if len(content) <= 300 else content[:300] + " ..."
            print(f"[工具返回] ({getattr(msg, 'name', '')}):\n{preview}\n")

    final = messages[-1]
    print("=" * 70)
    print("最终回答：")
    print(getattr(final, "content", ""))


if __name__ == "__main__":
    asyncio.run(main())
