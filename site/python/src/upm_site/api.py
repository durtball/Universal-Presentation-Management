"""Independent UPM Site FastAPI application boundary."""

from typing import Literal

from fastapi import FastAPI
from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    service: Literal["upm-site"] = "upm-site"
    status: Literal["foundation-ready"] = "foundation-ready"


def create_app() -> FastAPI:
    """Create the Site API without importing Central application state."""
    app = FastAPI(
        title="UPM Site API",
        version="0.1.0",
        description="UPM Site foundation API; operational endpoints are not implemented yet.",
    )

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    def health() -> HealthResponse:
        return HealthResponse()

    return app
