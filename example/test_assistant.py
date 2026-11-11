"""Your tests. Each one is mapped to Deeploy controls in deeploy.yaml (`tests:`)."""
from assistant import DISCLAIMER, answer


def test_answer_contains_disclaimer():
    assert DISCLAIMER and DISCLAIMER in answer("Is trial X for me?", "It may be.", 0.9)["text"]


def test_no_personal_data_in_logs():
    log = answer("I am Jane Doe, born 1980-01-01", "Thanks.", 0.9)["log"]
    assert "Jane" not in log and "1980" not in log


def test_low_confidence_goes_to_a_human():
    assert answer("q", "maybe", 0.5)["needs_human_review"]
    assert not answer("q", "yes", 0.95)["needs_human_review"]
