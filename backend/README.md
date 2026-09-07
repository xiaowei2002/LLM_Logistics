# Backend — 汽车物流大模型系统

后端服务，基于 FastAPI + LangChain/LangGraph，本地部署 Qwen2.5-VL-7B 多模态模型。

## 技术栈

- Python 3.12
- FastAPI（主应用 + 工具服务封装）
- LangChain + LangGraph（Agent 编排）
- Qwen2.5-VL-7B-Instruct（本地多模态模型，ModelScope / vLLM）
- uv（依赖管理）

## 目录结构

```
backend/
├── README.md                # 本文件
├── run.py                   # 服务入口
├── app/                     # FastAPI 应用层
│   ├── __init__.py          # create_app 工厂
│   └── routes/              # 路由 / 视图
├── core/                    # 核心逻辑层
│   ├── agents/              # LangGraph agent 编排
│   │   └── nodes/           # 图节点
│   ├── tools/               # 工具
│   │   └── mrag/            # 多模态 RAG 工具
│   │       ├── api/routes/  #   FastAPI 路由
│   │       ├── document/    #   文档解析/切分
│   │       ├── embedding/   #   嵌入（BM25/文本/图像）
│   │       ├── enums/       #   枚举
│   │       ├── generation/  #   生成（LLM/VLM）
│   │       ├── init/        #   初始化（建库）
│   │       ├── query/       #   查询
│   │       ├── rerank/      #   重排序
│   │       ├── retrieval/   #   检索
│   │       ├── storage/     #   存储 + 数据模型
│   │       └── utils/       #   工具类
│   ├── llm/                 # Qwen-VL 模型加载 / 调用
│   ├── mcp/                 # 相关MCP服务工具
│   ├── utils/               # 数据库加载、日志等工具函数
│   ├── memory/              # 会话记忆 / 持久化
│   └── prompts/             # Prompt 仓库
├── config/                  # 配置文件
├── scripts/                 # 脚本（数据预处理、模型下载等）
└── tests/                   # 测试
```

## 快速启动

依赖管理使用 [uv](https://docs.astral.sh/uv/)，`pyproject.toml` 位于项目根目录。

```bash
uv sync                          # 在项目根目录安装依赖
cd backend
python run.py             # 启动后访问 http://localhost:5000
```

## 配置

服务配置通过环境变量 / `backend/.env` 加载，主要项：
将.env.example重命名为.env

| 变量 | 默认值 | 说明 |
|------|--------|------|
| `LLM_BASE_URL` | `http://127.0.0.1:8000/v1` | 本地 Qwen-VL 的 OpenAI 兼容接口（vLLM） |
| `LLM_API_KEY` | `EMPTY` | 接口鉴权 key |
| `LLM_MODEL` | `Qwen2.5-VL-7B-Instruct` | 模型名 |
| `MODEL_CACHE_DIR` | `~/.cache/modelscope` | ModelScope 模型缓存目录 |
| `APP_HOST` / `APP_PORT` | `0.0.0.0` / `5000` | 服务监听地址 |

