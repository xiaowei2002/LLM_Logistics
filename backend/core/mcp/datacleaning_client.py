"""
MCP-Pandas 客户端。
通过 stdio 或 streamable HTTP 连接 mcp-pandas 数据清洗服务。
"""

import os

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv(override=True)


class DataCleaningMCPClient:
    """构建连接 mcp-pandas MCP 的客户端"""

    def __init__(self, transport: str = "streamable_http") -> None:
        if transport == "stdio":
            config = {
                "transport": "stdio",
                "command": os.getenv("PANDAS_MCP_COMMAND", "mcp-pandas"),
                "args": [],
            }
        else:
            config = {
                "transport": "streamable_http",
                "url": os.getenv("PANDAS_MCP_URL", "http://127.0.0.1:8080/mcp"),
            }
        self.client = MultiServerMCPClient({"mcp-pandas": config})

    async def get_tools(self):
        """加载工具"""
        return await self.client.get_tools()
