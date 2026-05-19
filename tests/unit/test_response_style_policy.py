from __future__ import annotations

from bookcraft.components.context.schemas import ContextPack, KnownFact
from bookcraft.components.response.style_policy import ResponseStylePolicy

_policy = ResponseStylePolicy.default()


def _fact(*, path: str, label: str, value: str) -> KnownFact:
    return KnownFact(path=path, label=label, value=value, confidence=0.95, source="user_stated")


def test_passes_human_specific_cover_design_response() -> None:
    text = (
        "Since your manuscript is finished and it’s children’s fiction, we can focus the cover "
        "around the story’s tone. Should it feel playful, magical, or more cinematic?"
    )
    report = _policy.evaluate(text=text)
    assert report.passed is True


def test_fails_generic_assistive_opener() -> None:
    text = "Sure! I can assist you with that. How can I help?"
    report = _policy.evaluate(text=text)
    assert report.passed is False
    assert any("banned_opener" in failure for failure in report.failures)


def test_fails_fake_excitement() -> None:
    text = "Absolutely! This is super exciting!!!"
    report = _policy.evaluate(text=text)
    assert report.passed is False
    assert "fake_excitement" in report.failures


def test_fails_internal_terms() -> None:
    text = "The backend classifier flagged your request and updated routing."
    report = _policy.evaluate(text=text)
    assert report.passed is False
    assert "internal_terms_detected" in report.failures


def test_fails_excessive_weak_phrases() -> None:
    text = "Maybe we can help, I think this is probably right, and possibly useful."
    report = _policy.evaluate(text=text)
    assert report.passed is False
    assert any("excessive_weak_language" in failure for failure in report.failures)


def test_fails_missing_specificity_when_context_known() -> None:
    context_pack = ContextPack(
        active_service="cover_design_illustration",
        active_genre="children's fiction",
        known_facts=[
            _fact(path="project.service", label="service", value="cover design"),
            _fact(path="project.genre", label="genre", value="children's fiction"),
        ],
    )
    report = _policy.evaluate(text="Can you share more details?", context_pack=context_pack)
    assert report.passed is False
    assert "missing_specificity_known_context" in report.failures


def test_passes_blocked_tool_safe_message() -> None:
    text = "I should confirm a few details before moving ahead with that."
    report = _policy.evaluate(text=text)
    assert report.passed is True


def test_one_question_rule_is_respected() -> None:
    text = (
        "Since your manuscript is finished, we can move to editing scope. "
        "What word count are you targeting? Do you want developmental or copy editing?"
    )
    report = _policy.evaluate(text=text)
    assert report.passed is False
    assert any("multiple_questions" in failure for failure in report.failures)
