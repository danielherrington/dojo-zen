"""Labelled precision/recall corpus for urgent-alert classification (DAN-164)."""

import json
from pathlib import Path

import pytest

from dojo.alert_evaluator import AlertEvaluator, UrgencyLevel, find_guarded_match, HEALTH_EMERGENCY_PATTERNS

CORPUS = json.loads((Path(__file__).parent / "fixtures" / "alert_corpus.json").read_text())["cases"]


def _classify(case):
    ev = AlertEvaluator()
    if case["kind"] == "message":
        return ev.evaluate_message({"id": "c", "sender_name": "Teacher", "body": case["text"]})
    return ev.evaluate_feed_item({"id": "c", "author_name": "School", "header": case.get("header", ""),
                                  "content_text": case["text"]})


@pytest.mark.parametrize("case", CORPUS, ids=[f"{c['expected']}:{c['text'][:48]}" for c in CORPUS])
def test_corpus_case(case):
    decision = _classify(case)
    assert decision.urgency == UrgencyLevel(case["expected"]), decision.reason


def test_corpus_is_balanced():
    msgs = [c for c in CORPUS if c["kind"] == "message"]
    assert sum(c["expected"] == "immediate" for c in msgs) >= 20
    assert sum(c["expected"] == "routine" for c in msgs) >= 20


def test_negation_is_clause_scoped():
    # "no fever" is negated, but the vomiting in the next clause still alerts
    assert find_guarded_match(HEALTH_EMERGENCY_PATTERNS, "no fever, but she vomited at lunch") == r"\bvomi[tt]"
    assert find_guarded_match(HEALTH_EMERGENCY_PATTERNS, "she was not hurt at all") is None


def test_strong_signal_overrides_policy_guard():
    text = "reminder: if your child is sick we call home. leo has a fever, please come pick him up."
    assert find_guarded_match(HEALTH_EMERGENCY_PATTERNS, text) is not None


@pytest.mark.parametrize("text", [
    "She has a fever and if she gets worse we will let you know.",
    "Leo has a fever of 101 so keep him home tomorrow too.",
    "Leo was hurt at recess, not badly, but he is crying and asking for you.",
])
def test_specific_child_emergencies_still_alert(text):
    decision = AlertEvaluator().evaluate_message({"id": "x", "sender_name": "Teacher", "body": text})
    assert decision.urgency == UrgencyLevel.IMMEDIATE, decision.reason
