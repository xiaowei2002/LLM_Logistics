from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="llm-logistic")

    from app.routes.health import router

    app.include_router(router)
    return app
