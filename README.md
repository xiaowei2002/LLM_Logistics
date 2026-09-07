# 汽车物流大模型系统

## 1. 环境配置

开发环境：
- Python：3.12
- torch： 2.5.1
- LLM： Qwen-2.5-VL-7b-Instruct
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

## 2. 项目文件结构
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
│   └── pdf/                #   PDF
├── UI/                     # 前端工程
└── backend/                # 后端服务
```