# 汽车物流大模型系统

## 1. 环境配置

开发环境：
- Python：3.12
- Node.js：22（前端构建）
- torch： 2.5.1
- LLM： Qwen-2.5-VL-7b-Instruct
- 数据库：Neo4j 4.x（知识图谱存储）
- 前端：Vue 3 + Vite
- 后端：FastAPI
- Agent：Langchain

### uv环境构建
使用 [uv](https://docs.astral.sh/uv/) 管理依赖：
```bash
uv sync
```

### conda环境构建
```bash
# 安装torch
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 -f https://mirrors.aliyun.com/pytorch-wheels/cu121/

# 安装其他依赖
conda create -n <env_name> python=3.12
conda activate <env_name>
pip install -r requirements.txt
```

### 前端环境依赖构建
```bash
cd UI
npm install
```

## 2. 知识图谱数据库初始化

知识图谱数据存储在 Neo4j 中，首次部署需完成以下一次性初始化：

1. 安装并启动 Neo4j（4.x），首次访问 http://localhost:7474 设置数据库密码
2. 在 `backend/.env` 中配置连接信息（该文件不入库，需自行创建）：
   ```
   NEO4J_URI=bolt://localhost:7687
   NEO4J_USER=neo4j
   NEO4J_PASSWORD=<你的密码>
   ```
3. 导入图谱数据：
   ```bash
   python knowledge/import_graph.py                 # 默认导入 knowledge/merged_graph.json
   python knowledge/import_graph.py 自定义数据.json   # 或指定其他数据文件
   ```
   脚本会清空旧数据后全量导入，可重复执行。

数据文件格式约定：
```json
{
  "entities": ["实体1", "实体2"],
  "relations": [["主体", "关系", "客体"]]
}
```

## 3. 项目文件结构
```
LLM_Logistic/
├── .env                    # 环境变量配置
├── .python-version         # Python 版本声明
├── README.md               # 项目说明文档
├── pyproject.toml          # uv 项目配置与依赖声明
├── requirements.txt        # pip 依赖清单
├── uv.lock                 # uv 依赖锁定文件
├── knowledge/              # 知识库
│   ├── docs/               #   文档
│   ├── imgs/               #   图片
│   ├── pdf/                #   PDF
│   ├── merged_graph.json   #   知识图谱数据（导入源）
│   └── import_graph.py     #   图谱导入脚本
├── UI/                     # 前端工程
└── backend/                # 后端服务
```

## 4. 项目运行
```bash
# 1. 启动 Neo4j（知识图谱查询依赖，需最先启动）
neo4j console

# 2. 启动后端服务
cd backend && python run.py

# 3. 启动前端
cd UI && npm run dev
```
启动后访问 http://localhost:5173，登录后通过左下角用户菜单进入「知识图谱」页面。