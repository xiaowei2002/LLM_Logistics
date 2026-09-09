from fastapi import FastAPI

from app.routes.chat import router as chat_router
from app.routes.demand_forecast import router as demand_forecast_router
from app.routes.health import router as health_router
from app.routes.settings import router as settings_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="llm-logistic",
        version="0.1.0",
    )

    app.include_router(health_router)
    app.include_router(chat_router)
    app.include_router(settings_router)
    app.include_router(demand_forecast_router)

    return app