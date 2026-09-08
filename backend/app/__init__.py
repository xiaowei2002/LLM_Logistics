from fastapi import FastAPI
from app.routes.chat import router as chat_router
from app.routes.health import router as health_router
from app.routes.demand_forecast import router as demand_forecast_router

def create_app() -> FastAPI:
    app = FastAPI(
        title="llm-logistic",
        version="0.1.0",
    )

    routers = [health_router, chat_router, demand_forecast_router]
    for router in routers:
        app.include_router(router)
    return app

