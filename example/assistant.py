"""A stand-in for your AI system. Break a control here (e.g. empty DISCLAIMER) to see a failing run."""

DISCLAIMER = "This is not medical advice."
CONFIDENCE_THRESHOLD = 0.7


def answer(question: str, model_reply: str, confidence: float) -> dict:
    """Wrap a model reply the way the product shows it to a patient."""
    return {
        "text": f"{model_reply}\n\n{DISCLAIMER}".strip(),
        "needs_human_review": confidence < CONFIDENCE_THRESHOLD,
        "log": f"question_length={len(question)} confidence={confidence:.2f}",   # never the question itself
    }
