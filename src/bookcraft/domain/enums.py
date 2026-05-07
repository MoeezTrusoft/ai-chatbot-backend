from enum import StrEnum


class Source(StrEnum):
    USER_STATED = "user_stated"
    USER_CONFIRMED = "user_confirmed"
    AI_EXTRACTED = "ai_extracted"
    CSR_ENTERED = "csr_entered"
    SYSTEM = "system"


class ServiceCategory(StrEnum):
    GHOSTWRITING = "ghostwriting"
    EDITING_PROOFREADING = "editing_proofreading"
    COVER_DESIGN_ILLUSTRATION = "cover_design_illustration"
    INTERIOR_FORMATTING = "interior_formatting"
    AUDIOBOOK_PRODUCTION = "audiobook_production"
    PUBLISHING_DISTRIBUTION = "publishing_distribution"
    MARKETING_PROMOTION = "marketing_promotion"
    AUTHOR_WEBSITE = "author_website"
    VIDEO_TRAILER = "video_trailer"


class QueryIntentType(StrEnum):
    GREETING = "greeting"
    SERVICE_QUESTION = "service_question"
    PRICING_QUESTION = "pricing_question"
    TIMELINE_QUESTION = "timeline_question"
    PORTFOLIO_REQUEST = "portfolio_request"
    CONSULTATION_REQUEST = "consultation_request"
    NDA_REQUEST = "nda_request"
    AGREEMENT_REQUEST = "agreement_request"
    REVISION_QUESTION = "revision_question"
    PAYMENT_QUESTION = "payment_question"
    PUBLISHING_PLATFORM_QUESTION = "publishing_platform_question"
    MANUSCRIPT_STATUS_UPDATE = "manuscript_status_update"
    CONTACT_INFO_PROVIDED = "contact_info_provided"
    COMPLAINT_OR_OBJECTION = "complaint_or_objection"
    READY_TO_BUY = "ready_to_buy"
    UNCLEAR = "unclear"
    SPAM_OR_ABUSE = "spam_or_abuse"
    OFF_TOPIC = "off_topic"


class SalesStage(StrEnum):
    NEW = "new"
    EXPLORING = "exploring"
    SERVICE_DISCOVERY = "service_discovery"
    SCOPING = "scoping"
    QUOTE_REQUESTED = "quote_requested"
    QUOTED = "quoted"
    NEGOTIATION = "negotiation"
    NDA_REQUESTED = "nda_requested"
    AGREEMENT_REQUESTED = "agreement_requested"
    CLOSED_WON = "closed_won"
    CLOSED_LOST = "closed_lost"


class ManuscriptStatus(StrEnum):
    IDEA_ONLY = "idea_only"
    OUTLINE = "outline"
    PARTIAL_DRAFT = "partial_draft"
    COMPLETED_DRAFT = "completed_draft"
    EDITED = "edited"
    PUBLISHED = "published"
    UNKNOWN = "unknown"


class ContactMethod(StrEnum):
    EMAIL = "email"
    PHONE = "phone"
    SMS = "sms"
    WHATSAPP = "whatsapp"
    UNKNOWN = "unknown"


class ToolClass(StrEnum):
    READ = "read"
    WRITE_STATE = "write_state"
    EXTERNAL_SIDE_EFFECT = "external_side_effect"
    HIGH_STAKES_DOCUMENT = "high_stakes_document"


class ToolInvocationStatus(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    DEFERRED = "deferred"
    IDEMPOTENT_REPLAY = "idempotent_replay"

