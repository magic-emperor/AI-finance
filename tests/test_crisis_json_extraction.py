"""
Pins the fix for the crisis_classifier JSON-parse bug.

The old regex (brace, non-brace characters, brace) could not match a nested object.
The prompt asks Gemini for a nested "affected_sectors" object, so a well-behaved
reply was parsed as just the inner object and crisis_type fell back to NONE --
crisis classification could not work. The existing router tests never saw this
because they mock _call_gemini wholesale.
"""
from __future__ import annotations

from market_agent.learning.crisis_classifier import extract_json_object

REPLY = ('{"crisis_type": "WAR_GEOPOLITICAL", "severity": "HIGH", '
         '"affected_sectors": {"GOLD": "UP", "AVIATION": "DOWN"}, '
         '"gemini_confidence": 0.85, "reasoning": "x", "is_likely_real": true}')


def test_nested_object_is_parsed_whole_not_just_the_inner_part():
    data = extract_json_object(REPLY)
    assert data["crisis_type"] == "WAR_GEOPOLITICAL"
    assert data["severity"] == "HIGH"
    assert data["affected_sectors"] == {"GOLD": "UP", "AVIATION": "DOWN"}


def test_markdown_code_fences_and_chatter_are_tolerated():
    data = extract_json_object("Sure!\n```json\n" + REPLY + "\n```\nHope that helps.")
    assert data["crisis_type"] == "WAR_GEOPOLITICAL"


def test_empty_affected_sectors_still_parses():
    data = extract_json_object('{"crisis_type": "NONE", "severity": "NONE", "affected_sectors": {}}')
    assert data == {"crisis_type": "NONE", "severity": "NONE", "affected_sectors": {}}


def test_garbage_and_non_objects_return_none():
    for bad in (None, "", "no json here", "{broken", "[1, 2, 3]", "{}}{"):
        assert extract_json_object(bad) in (None, {}), bad
