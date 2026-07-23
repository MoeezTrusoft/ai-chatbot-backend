from pydantic import BaseModel, ConfigDict


class LanguageDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    language: str
    is_english: bool
    confidence: float
    source: str
    redirect_message: str | None = None
    # True when the message mixes English with another language. Such turns are NOT
    # hard-redirected: the bot answers the English part and politely asks for the rest
    # in English (that reply is Claude-generated, never a template).
    is_mixed: bool = False
