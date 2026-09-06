# 汽车物流大模型系统

## 1. 环境配置

开发环境：
- Python：3.12
- torch： 2.5.1
- LLM： Qwen-2.5-VL-7b-Instruct
- 前端：Vue 3 + Vite（Node.js 20.19+ / 22.12+）
- 后端：Flask
- Agent：Langchain

### 1.1 Python 环境

#### uv环境构建
使用 [uv](https://docs.astral.sh/uv/) 管理依赖：
```bash
uv sync
```

#### conda环境构建
```bash
# 安装torch
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 -f https://mirrors.aliyun.com/pytorch-wheels/cu121/

# 安装其他依赖
conda create -n <env_name> python=3.12
conda activate <env_name>
pip install -r requirements.txt
```

### 1.2 Node.js / npm 环境

前端使用 Vue 3 + Vite，需要 **Node.js 20.19+ 或 22.12+**（Vite 7 的最低要求），npm 随 Node.js 一同安装。

从 [nodejs.org](https://nodejs.org/) 下载 LTS 安装包，或使用 nvm / fnm 管理版本。安装完成后校验：

```bash
node -v
npm -v
```

依赖下载缓慢时可切换国内镜像：

```bash
npm config set registry https://registry.npmmirror.com
```

## 2. 前端项目构建

前端工程位于 `UI/` 目录，所有命令均在该目录下执行：

```bash
cd UI
```

安装依赖（首次或依赖变更后执行）：

```bash
npm install
```

启动开发服务器，默认地址 `http://localhost:5173`，支持热更新：

```bash
npm run dev
```

生产构建，产物输出到 `UI/dist/`：

```bash
npm run build
```

本地预览构建产物：

```bash
npm run preview
```

当前前端仅包含页面部分，未接入后端接口。更多前端细节见 [UI/README.md](UI/README.md)。

## 3. 登录与权限控制

系统当前为**单账号权限控制**，账号信息由配置文件 `UI/src/config/auth.json` 管理：

```json
{
  "account": {
    "username": "admin",
    "password": "logistics2025",
    "displayName": "管理员"
  },
  "session": {
    "storageKey": "llm-logistics-auth",
    "expireHours": 12
  }
}
```

- 修改账号密码只需编辑该文件，开发模式热更新生效，生产环境需重新执行 `npm run build`；
- 未登录访问任意页面自动跳转登录页，登录成功后回到原页面；
- 登录态写入浏览器 localStorage，刷新免登录，超过 `expireHours`（默认 12 小时）后失效；
- 首页右上角「退出登录」清除登录态并返回登录页。

> 注意：校验在前端完成，账号密码会随构建产物下发到浏览器，仅适用于内部演示。
> 正式部署需将校验逻辑迁移到后端接口。

## 4. 项目文件结构
```
LLM_Logistic/
├── .env                    # 环境变量配置（API Key等敏感信息）
├── README.md               # 项目说明文档
├── pyproject.toml          # uv 项目配置与依赖声明
├── requirements.txt        # 依赖清单
├── uv.lock                 # uv依赖锁定文件
└── UI/                     # 前端工程（Vue 3 + Vite）
    ├── index.html          # Vite 入口 HTML
    ├── package.json        # 前端依赖与脚本
    ├── vite.config.js      # Vite 配置（别名、端口）
    ├── public/             # 静态资源
    └── src/
        ├── main.js         # 应用入口
        ├── App.vue         # 根组件
        ├── config/         # 账号等配置文件
        ├── router/         # 路由与登录守卫
        ├── views/          # 页面视图（登录页、首页）
        ├── components/     # 通用组件
        ├── stores/         # Pinia 状态管理
        └── assets/         # 样式与静态资源
```
