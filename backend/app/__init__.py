from fastapi import FastAPI


def create_app() -> FastAPI:

    app = FastAPI(
        title="llm-logistic",
        version="0.1.0",
    )

    from app.routes.health import router as health_router
    from app.routes.demand_forecast import (
        router as demand_forecast_router,
    )

    app.include_router(
        health_router
    )

    app.include_router(
        demand_forecast_router
    )

    return app