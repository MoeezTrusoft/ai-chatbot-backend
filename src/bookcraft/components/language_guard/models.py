from pydantic import BaseModel, ConfigDict


class LanguageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    is_english: bool
    confidence: float
    source: str
    redirect_message: str | None = None

