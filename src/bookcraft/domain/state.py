from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from bookcraft.domain.enums import ContactMethod, ManuscriptStatus, SalesStage, ServiceCategory
from bookcraft.domain.meta import FieldMeta


class PersonalInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    email: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    phone: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    preferred_contact_method: FieldMeta[ContactMethod] = Field(
        default_factory=FieldMeta[ContactMethod]
    )
    timezone: FieldMeta[str] = Field(default_factory=FieldMeta[str])


class ServiceInterest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: FieldMeta[ServiceCategory] = Field(default_factory=FieldMeta[ServiceCategory])
    subservices: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class ProjectInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    genre: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    sub_genre: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    synopsis: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    word_count: FieldMeta[int] = Field(default_factory=FieldMeta[int])
    page_count: FieldMeta[int] = Field(default_factory=FieldMeta[int])
    manuscript_status: FieldMeta[ManuscriptStatus] = Field(
        default_factory=FieldMeta[ManuscriptStatus]
    )
    target_completion_date: FieldMeta[datetime] = Field(default_factory=FieldMeta[datetime])
    services_discussed: list[ServiceInterest] = Field(default_factory=list)


class CommercialInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    budget_range: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    timeline_expectation: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    latest_quote_id: FieldMeta[str] = Field(default_factory=FieldMeta[str])


class DocumentsInfo(BaseModel):
    model_config = ConfigDict(extra="forbid")

    nda_requested: FieldMeta[bool] = Field(default_factory=FieldMeta[bool])
    agreement_requested: FieldMeta[bool] = Field(default_factory=FieldMeta[bool])
    latest_nda_document_id: FieldMeta[str] = Field(default_factory=FieldMeta[str])
    latest_agreement_document_id: FieldMeta[str] = Field(default_factory=FieldMeta[str])


class ThreadState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: int = 1
    sales_stage: FieldMeta[SalesStage] = Field(default_factory=FieldMeta[SalesStage])
    personal: PersonalInfo = Field(default_factory=PersonalInfo)
    project: ProjectInfo = Field(default_factory=ProjectInfo)
    commercial: CommercialInfo = Field(default_factory=CommercialInfo)
    documents: DocumentsInfo = Field(default_factory=DocumentsInfo)
    rolling_summary: str = ""

