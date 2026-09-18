"""
数据清洗智能体
通过 MCP 连接 mcp-pandas 服务，对 ERP/WMS 原始数据做质量诊断与清洗。
"""
from pathlib import Path

from langchain.agents import create_agent

from core.llm import LLMClient, get_llm
from core.mcp.datacleaning_client import DataCleaningMCPClient
from core.utils.yaml_loader import load_yaml

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "cleaning_agent.yaml"


class CleaningAgent:
    """基于 mcp-pandas 的数据清洗智能体。"""

    def __init__(self, client: LLMClient | None = None) -> None:
        self.client = client or get_llm()
        self.mcp = DataCleaningMCPClient()
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

    async def clean(self, instruction: str) -> str:
        """执行一次自然语言数据清洗任务，返回清洗说明。"""
        agent = await self.ensure_agent()
        result = await agent.ainvoke(
            {"messages": [{"role": "user", "content": instruction}]}
        )
        messages = result.get("messages", [])
        if not messages:
            return ""
        return str(getattr(messages[-1], "content", ""))
