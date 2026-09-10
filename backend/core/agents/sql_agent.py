"""
数据读取智能体
通过 MCP 连接 mysql-mcp-server，使用 SQL 读取 ERP/WMS 数据。
"""
from pathlib import Path

from langchain.agents import create_agent

from core.llm import LLMClient, get_llm
from core.mcp.mysql_client import MySQLMCPClient
from core.utils.yaml_loader import load_yaml

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "sql_agent.yaml"


class SQLAgent:
    """基于mysql-mcp-server的数据读取智能体。"""

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or get_llm()
        self.mcp = MySQLMCPClient()
        self.agent = None
        self.system_prompt = load_yaml(PROMPT_PATH)["system_prompt"]

    async def ensure_agent(self):
        """首次调用时加载MCP工具并构建 Agent。"""
        if self.agent is None:
            tools = await self.mcp.get_tools()
            self.agent = create_agent(
                model=self.client.model,
                tools=tools,
                system_prompt=self.system_prompt,
            )
        return self.agent

    async def query(self, question: str) -> str:
        """执行一次自然语言数据查询，返回最终回答。"""
        agent = await self.ensure_agent()
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": question}]}
        )
        messages = result.get("messages", [])
        if not messages:
            return ""
        return str(getattr(messages[-1], "content", ""))
