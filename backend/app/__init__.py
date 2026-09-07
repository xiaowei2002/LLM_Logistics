from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="llm-logistic")

    from app.routes.chat import router as chat_router
    from app.routes.health import router as health_router

    app.include_router(health_router)
    app.include_router(chat_router)
    return app
