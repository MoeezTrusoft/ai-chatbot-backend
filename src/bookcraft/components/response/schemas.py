from pydantic import BaseModel, ConfigDict, Field


class ResponseDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    source: str


class FormattedBubble(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    bubble_index: int
    rich_segments: list[dict[str, str]] = Field(default_factory=list)

