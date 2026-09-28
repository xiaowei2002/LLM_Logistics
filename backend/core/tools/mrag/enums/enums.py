"""
==========================================================================
全局枚举（把散落在 query/service 里的字符串常量收口到一处）

- SearchMode：检索模式
- ChunkType ：文档块类型
- StoreType ：存储引擎类型

用 str, Enum：枚举成员本身就是字符串，可直接 == "auto" 比较、可 JSON 序列化。
用法：
    from core.tools.mrag.enums import SearchMode
    SearchMode.AUTO          # <SearchMode.AUTO: 'auto'>
    SearchMode.AUTO.value    # 'auto'
==========================================================================
"""
from enum import Enum


class SearchMode(str, Enum):
    """检索模式：auto / rag / graphrag / hybrid。"""

    AUTO = "auto"
    RAG = "rag"            # 文档向量检索
    GRAPHRAG = "graphrag"  # 知识图谱检索
    HYBRID = "hybrid"      # 文档向量 + 知识图谱融合


class ChunkType(str, Enum):
    """文档块类型：loader.py Chunk.type 的合法取值。"""

    TEXT = "text"
    IMAGE = "image"
    TABLE = "table"
    EQUATION = "equation"
    CHART = "chart"
    CODE = "code"


class StoreType(str, Enum):
    """存储引擎类型：storage 两个 store 的 engine 标识。"""

    VECTOR = "vector"
    GRAPH = "graph"
