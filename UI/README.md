# 基于大模型多主体协同的智慧物流系统

汽车物流大模型系统的前端服务层。
前后端分离，本目录**只负责前端页面**，不包含后端业务逻辑。

## 项目结构
```
UI/
├── app.py                  # Flask 入口，渲染首页
├── templates/
│   └── index.html          # 页面模板
└── static/
    ├── css/
    │   └── style.css       # 样式
    └── js/
        └── app.js          # 前端交互逻辑
```

## 运行
在项目根目录（`LLM_Logistic/`）下：
```bash
# 使用已激活的虚拟环境
python UI/app.py
```
或进入 `UI/` 目录后：
```bash
python app.py
```

