from pydantic import BaseModel, ConfigDict


class HealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    app: str
    environment: str


class DependencyStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    detail: str | None = None


class ReadinessResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    status: str
    dependencies: dict[str, DependencyStatus]

