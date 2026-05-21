"""ServiceMetadataRegistry — per-service extractable metadata key definitions.

Defines what metadata can be extracted for each BookCraft service,
with accepted values and extraction priority.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Registry structure: service_key -> {metadata_key: {accepted_values, priority, ...}}
# ---------------------------------------------------------------------------

SERVICE_METADATA_REGISTRY: dict[str, dict[str, dict[str, Any]]] = {
    "ghostwriting": {
        "source_material_type": {
            "accepted": [
                "idea",
                "rough_notes",
                "journal_entries",
                "voice_memo",
                "outline",
                "partial_draft",
                "draft",
                "completed_manuscript",
            ],
            "priority": 1,
        },
        "story_type_status": {
            "accepted": ["fiction", "memoir", "business_self_help", "hybrid", "uncertain"],
            "priority": 1,
        },
        "desired_voice": {
            "accepted": [
                "conversational",
                "professional",
                "inspirational",
                "humorous",
                "academic",
                "personal",
                "brand_voice",
                "unknown",
            ],
            "priority": 2,
        },
        "target_audience": {
            "accepted": [
                "children",
                "young_adult",
                "adults",
                "entrepreneurs",
                "professionals",
                "faith_based",
                "general_readers",
                "unknown",
            ],
            "priority": 2,
        },
        "author_involvement_level": {
            "accepted": ["hands_off", "collaborative", "interview_based", "heavy_involvement"],
            "priority": 3,
        },
        "interview_required": {"accepted": [True, False], "priority": 3},
        "chapter_outline_available": {"accepted": [True, False], "priority": 3},
        "research_required": {"accepted": [True, False], "priority": 3},
    },
    "editing_proofreading": {
        "editing_level": {
            "accepted": [
                "developmental_editing",
                "line_editing",
                "copyediting",
                "proofreading",
                "not_sure",
            ],
            "priority": 1,
        },
        "manuscript_status": {
            "accepted": [
                "idea",
                "rough_notes",
                "outline",
                "partial_draft",
                "draft",
                "completed",
                "published",
            ],
            "priority": 1,
        },
        "word_count": {"accepted": "integer", "priority": 1},
        "page_count": {"accepted": "integer", "priority": 1},
        "dialect": {
            "accepted": [
                "us_english",
                "uk_english",
                "canadian_english",
                "australian_english",
                "not_sure",
            ],
            "priority": 2,
        },
        "style_guide": {
            "accepted": ["chicago", "apa", "mla", "ap", "custom", "not_sure"],
            "priority": 3,
        },
        "file_received": {"accepted": [True, False], "priority": 2},
        "deadline": {"accepted": "string", "priority": 2},
    },
    "cover_design_illustration": {
        "cover_format": {
            "accepted": [
                "ebook_cover",
                "paperback_cover",
                "hardcover_cover",
                "full_wrap",
                "front_cover_only",
                "dust_jacket",
                "not_sure",
            ],
            "priority": 1,
        },
        "front_back_spine_needed": {"accepted": [True, False], "priority": 1},
        "trim_size": {"accepted": "string", "priority": 2},
        "visual_style": {
            "accepted": [
                "minimalist",
                "illustrated",
                "photographic",
                "luxury",
                "bold_typographic",
                "cinematic",
                "children_illustration",
                "not_sure",
            ],
            "priority": 1,
        },
        "reference_titles": {"accepted": "list_of_strings", "priority": 3},
        "genre_visual_direction": {"accepted": "string", "priority": 2},
        "audience": {"accepted": "string", "priority": 2},
    },
    "interior_formatting": {
        "book_formats": {
            "accepted": ["ebook", "paperback", "hardcover", "large_print"],
            "priority": 1,
        },
        "trim_size": {"accepted": "string", "priority": 2},
        "platforms": {
            "accepted": ["amazon_kdp", "ingramspark", "draft2digital"],
            "priority": 1,
        },
        "image_heavy": {"accepted": [True, False], "priority": 2},
        "tables_or_footnotes": {"accepted": [True, False], "priority": 2},
        "ebook_required": {"accepted": [True, False], "priority": 1},
        "print_required": {"accepted": [True, False], "priority": 1},
    },
    "publishing_distribution": {
        "publishing_platforms": {
            "accepted": [
                "amazon_kdp",
                "ingramspark",
                "barnes_and_noble",
                "kobo",
                "apple_books",
                "google_play_books",
                "draft2digital",
                "direct_website",
                "audible_acx",
            ],
            "priority": 1,
        },
        "book_formats": {
            "accepted": ["ebook", "paperback", "hardcover", "audiobook", "large_print"],
            "priority": 1,
        },
        "isbn_status": {
            "accepted": ["has_isbn", "needs_isbn", "not_sure"],
            "priority": 1,
        },
        "metadata_ready": {"accepted": [True, False], "priority": 2},
        "categories_keywords_ready": {"accepted": [True, False], "priority": 2},
        "territories": {
            "accepted": ["worldwide", "us_only", "uk", "canada", "australia", "not_sure"],
            "priority": 2,
        },
        "author_account_status": {
            "accepted": [
                "has_kdp",
                "needs_setup",
                "has_ingramspark",
                "needs_guidance",
                "not_sure",
            ],
            "priority": 3,
        },
    },
    "marketing_promotion": {
        "launch_stage": {
            "accepted": ["pre_launch", "launch", "post_launch", "relaunch", "not_sure"],
            "priority": 1,
        },
        "campaign_goal": {
            "accepted": [
                "awareness",
                "sales",
                "reviews",
                "bestseller_push",
                "author_branding",
                "lead_generation",
            ],
            "priority": 1,
        },
        "target_readers": {"accepted": "string", "priority": 2},
        "budget_range": {"accepted": "string", "priority": 2},
        "channels": {
            "accepted": [
                "amazon_ads",
                "meta_ads",
                "tiktok",
                "instagram",
                "linkedin",
                "email",
                "pr",
                "influencer",
                "not_sure",
            ],
            "priority": 1,
        },
        "assets_available": {"accepted": [True, False], "priority": 2},
        "reviews_status": {
            "accepted": ["no_reviews", "some_reviews", "many_reviews", "not_sure"],
            "priority": 2,
        },
    },
    "audiobook_production": {
        "audio_status": {
            "accepted": [
                "needs_recording",
                "has_audio",
                "needs_editing",
                "needs_mastering",
                "not_sure",
            ],
            "priority": 1,
        },
        "narrator_preference": {
            "accepted": ["male", "female", "neutral", "author_voice", "not_sure"],
            "priority": 2,
        },
        "accent_preference": {"accepted": "string", "priority": 3},
        "script_ready": {"accepted": [True, False], "priority": 2},
        "distribution_platform": {
            "accepted": ["audible_acx", "findaway", "spotify", "other", "not_sure"],
            "priority": 1,
        },
        "finished_audio_available": {"accepted": [True, False], "priority": 1},
    },
    "author_website": {
        "domain_status": {
            "accepted": ["has_domain", "needs_domain", "not_sure"],
            "priority": 1,
        },
        "pages_needed": {
            "accepted": [
                "home",
                "about",
                "books",
                "contact",
                "blog",
                "store",
                "media_kit",
                "booking",
            ],
            "priority": 2,
        },
        "store_needed": {"accepted": [True, False], "priority": 2},
        "newsletter_needed": {"accepted": [True, False], "priority": 2},
        "lead_magnet": {"accepted": [True, False], "priority": 3},
        "booking_form_needed": {"accepted": [True, False], "priority": 2},
    },
    "video_trailer": {
        "duration": {"accepted": "string", "priority": 1},
        "style": {
            "accepted": [
                "cinematic",
                "animated",
                "typography",
                "social_reel",
                "author_intro",
                "not_sure",
            ],
            "priority": 1,
        },
        "voiceover_needed": {"accepted": [True, False], "priority": 2},
        "assets_available": {"accepted": [True, False], "priority": 2},
        "platform": {
            "accepted": [
                "instagram",
                "tiktok",
                "youtube",
                "website",
                "amazon_author_page",
                "not_sure",
            ],
            "priority": 1,
        },
        "deadline": {"accepted": "string", "priority": 2},
    },
}


def get_service_keys(service: str) -> list[str]:
    """Return all extractable metadata key names for a service."""
    return list(SERVICE_METADATA_REGISTRY.get(service, {}).keys())
