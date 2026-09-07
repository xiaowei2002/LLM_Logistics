# 前端 - 物流智能体

基于大模型多主体协同的智慧物流系统的前端工程，采用 **Vue 3 + Vite** 构建。


## 1. 目录结构

```
UI/
├── index.html              # Vite 入口 HTML（挂载点 #app）
├── package.json            # 依赖与脚本
├── package-lock.json       # 依赖锁定文件
├── vite.config.js          # Vite 配置（@ 别名、开发端口）
├── jsconfig.json           # 编辑器路径提示（@ -> src）
├── public/                 # 静态资源，构建时原样拷贝
│   └── favicon.svg
└── src/
    ├── main.js             # 应用入口：挂载 Vue、Pinia、Router
    ├── App.vue             # 根组件（RouterView）
    ├── config/
    │   └── auth.json       # 账号配置文件（唯一账号 + 登录态有效期）
    ├── router/
    │   └── index.js        # 路由表与登录守卫
    ├── views/
    │   ├── LoginView.vue   # 登录页
    │   └── HomeView.vue    # 首页
    ├── components/
    │   ├── AppHero.vue     # Logo 与标语
    │   ├── TaskInput.vue   # 任务输入框
    │   └── icons/          # SVG 图标组件
    ├── stores/
    │   └── auth.js         # 登录状态（校验、持久化、退出）
    └── assets/styles/
        ├── variables.css   # 设计变量（颜色、圆角、阴影）
        └── main.css        # 全局基础样式
```

## 2. 环境准备

需要 **Node.js 20.19+ / 22.12+**（Vite 7 要求），npm 随 Node.js 一同安装。

从 [nodejs.org](https://nodejs.org/) 下载 LTS 安装包，或使用版本管理工具（nvm / fnm）。安装后校验：

```bash
node -v
npm -v
```

如需加速依赖下载，可配置国内镜像：

```bash
npm config set registry https://registry.npmmirror.com
```

## 3. 安装与运行

```bash
# 进入前端目录
cd UI

# 安装依赖
npm install

# 启动开发服务器（默认 http://localhost:5173，自动打开浏览器）
npm run dev
```

## 4. 登录与权限控制

系统当前为**单账号权限控制**，账号信息集中在配置文件 `src/config/auth.json` 中管理：

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

| 字段 | 说明 |
| --- | --- |
| `account.username` | 登录账号 |
| `account.password` | 登录密码 |
| `account.displayName` | 登录后页面右上角显示的名称 |
| `session.storageKey` | 登录态在 localStorage 中的键名 |
| `session.expireHours` | 登录态有效期（小时），过期后需重新登录 |

