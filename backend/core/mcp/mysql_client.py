"""
MySQL MCP 客户端。
通过 SSE 或 stdio 连接 mysql-mcp-server。
"""

import os

from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient

load_dotenv(override=True)


class MySQLMCPClient:
    """构建连接 MySQL MCP的客户端"""

    def __init__(self, transport: str = "sse") -> None:
        if transport == "stdio":
            config = {
                "transport": "stdio",
                "command": "uvx",
                "args": ["--from", "mysql-mcp-server", "mysql_mcp_server"],
                "env": {k: os.getenv(k, "") for k in
                        ("MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER",
                         "MYSQL_PASSWORD", "MYSQL_DATABASE")},
            }
        else:
            config = {
                "transport": "sse",
                "url": os.getenv("MYSQL_MCP_URL", "http://127.0.0.1:8000/sse"),
            }
        self.client = MultiServerMCPClient({"mysql": config})

    async def get_tools(self):
        """加载工具"""
        return await self.client.get_tools()
