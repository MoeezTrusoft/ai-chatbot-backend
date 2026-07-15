"""Splitting a multi-question turn into its individual questions (chat 5876).

An author opened with a 13-point due-diligence checklist — ISBN ownership, copyright,
exit terms, royalties, source files, fees. The bot answered none of them: the priority
classifier returns on its first regex hit, so twelve questions were never looked at,
and one incidental "guaranteed" routed the whole turn to a canned bestseller
disclaimer that was then repeated three times.
"""

from __future__ import annotations

from bookcraft.components.preprocessor.detectors.question_split import (
    MAX_QUESTIONS,
    split_questions,
)

# The message from chat 5876, verbatim in shape (no customer PII — this repo is public).
CHECKLIST = """Questions first???:Who owns the ISBN?
Do you keep 100% of your copyright?
Can you leave at any time?
Are there exclusive distribution clauses?
Who receives Amazon royalties?
How often are royalties paid?
Can you terminate the agreement?
Is the interior PDF yours forever?
Is the cover yours forever?
Are the source files (Word, InDesign, artwork) yours?
Are there ongoing annual fees?
What marketing is actually guaranteed versus merely offered?
What rights are they asking you to license?"""


def test_recovers_every_question_from_the_checklist():
    questions = split_questions(CHECKLIST)

    assert len(questions) == 13
    assert questions[0] == "Who owns the ISBN?"
    assert questions[-1] == "What rights are they asking you to license?"
    # The tail of the list is exactly what the old first-match-wins path dropped.
    assert "Are there ongoing annual fees?" in questions


def test_preamble_is_not_treated_as_a_question():
    # "Questions first???" ends in question marks but asks nothing.
    assert not any(q.lower().startswith("questions first") for q in split_questions(CHECKLIST))


def test_question_is_separated_from_a_leading_statement():
    assert split_questions("I have a draft. Who owns the ISBN?") == ["Who owns the ISBN?"]


def test_trailing_pleasantry_is_not_a_question():
    assert split_questions("Who owns the ISBN? thanks!") == ["Who owns the ISBN?"]


def test_duplicate_questions_collapse():
    questions = split_questions("Who owns the ISBN? Who owns the ISBN?")
    assert questions == ["Who owns the ISBN?"]


def test_single_question_yields_one_entry():
    # Callers branch on len() >= 2, so a normal turn must not look like a checklist.
    assert split_questions("How much does cover design cost?") == [
        "How much does cover design cost?"
    ]


def test_non_questions_yield_nothing():
    for text in ("yes", "", "Hello there!", "I need help with my cover", "ok thanks"):
        assert split_questions(text) == [], f"{text!r} should not parse as a question"


def test_fan_out_is_bounded():
    flood = " ".join(f"Who owns item number {i}?" for i in range(40))
    assert len(split_questions(flood)) == MAX_QUESTIONS
