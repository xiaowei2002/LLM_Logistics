from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="llm-logistic")

    from app.routes.chat import router as chat_router
    from app.routes.files import router as files_router
    from app.routes.health import router as health_router
    from app.routes.settings import effective_settings, router as settings_router
    from core.llm import reset_llm

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(files_router)
    app.include_router(settings_router)
    # 启动时应用网页端保存的配置覆盖值，避免重启后退回 .env 的模型
    reset_llm(effective_settings())
    return app
