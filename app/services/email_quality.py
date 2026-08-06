from __future__ import annotations

import re

SPAM_OR_DECEPTIVE = {
    "act now",
    "limited time",
    "guaranteed",
    "urgent",
    "free money",
    "risk-free",
    "no risk",
}
UNSUPPORTED_PHRASES = {
    "performs well on mobile",
    "performs well on security",
    "your site loads in",
    "will improve rankings",
    "will increase sales",
    "common in contractor sites",
}
PLACEHOLDER_PATTERN = re.compile(r"\[[A-Z][A-Z _-]{2,}\]|\{\{[^}]+\}\}")
URL_PATTERN = re.compile(r"https?://", re.IGNORECASE)
DECEPTIVE_SUBJECT_PATTERN = re.compile(r"^(?:re|fwd)\s*:", re.IGNORECASE)
WORD_RANGES = {
    0: (45, 110),
    1: (25, 80),
    2: (30, 85),
    3: (20, 65),
}


def review_email_message(
    business_name: str,
    stage: int,
    subject: str,
    body: str,
) -> dict[str, object]:
    normalized = " ".join(body.split())
    lower = normalized.lower()
    words = normalized.split()
    score = 100
    blockers: list[str] = []
    warnings: list[str] = []

    expected_greeting = f"hi {business_name.lower()} team"
    if expected_greeting not in lower[: max(80, len(expected_greeting) + 10)]:
        score -= 12
        warnings.append("Use a business-name team greeting without inventing an owner.")

    minimum, maximum = WORD_RANGES.get(stage, (20, 120))
    if len(words) < minimum:
        score -= 8
        warnings.append(f"Body is too short for stage {stage}; target {minimum}-{maximum} words.")
    elif len(words) > maximum:
        score -= min(25, 10 + (len(words) - maximum) // 5)
        warnings.append(f"Body is too long for stage {stage}; target {minimum}-{maximum} words.")

    if PLACEHOLDER_PATTERN.search(body):
        blockers.append("Unfilled placeholder detected.")
    if URL_PATTERN.search(body):
        blockers.append("AI body_core must not contain URLs; the system appends signed links.")
    clean_subject = " ".join(subject.split())
    subject_words = clean_subject.split()
    if DECEPTIVE_SUBJECT_PATTERN.search(clean_subject):
        blockers.append("Deceptive Re:/Fwd: subject prefix detected.")
    if not 3 <= len(subject_words) <= 7 or len(clean_subject) > 60:
        score -= 8
        warnings.append("Use a truthful 3-7 word subject under 60 characters.")
    if any(term in lower for term in SPAM_OR_DECEPTIVE):
        blockers.append("Spam, urgency, or guarantee language detected.")
    unsupported = [phrase for phrase in UNSUPPORTED_PHRASES if phrase in lower]
    if unsupported:
        blockers.append("Unsupported or overbroad claim detected: " + ", ".join(sorted(unsupported)))

    question_count = body.count("?")
    if stage in {0, 2} and question_count:
        blockers.append("Stages with an appended report link must not add a competing question CTA.")
    elif question_count > 1:
        blockers.append("More than one question CTA was detected.")

    if "search visibility" in lower:
        score -= 8
        warnings.append("Describe meta descriptions as search-snippet presentation, not ranking visibility.")
    if "no pressure" in lower:
        score -= 4
        warnings.append("Remove generic 'no pressure' filler.")
    if len(re.findall(r"\b(?:I|we|our|my)\b", body, re.IGNORECASE)) > 8:
        score -= 5
        warnings.append("Reduce sender-focused language and keep the email about the business.")

    if blockers:
        score = min(score, 69)
    return {
        "stage": stage,
        "score": max(0, min(100, score)),
        "word_count": len(words),
        "blockers": blockers,
        "warnings": warnings,
        "approved": not blockers and score >= 85,
    }


def review_email_bundle(business_name: str, messages: list[dict]) -> dict[str, object]:
    reviews = [
        review_email_message(
            business_name,
            int(message.get("stage", -1)),
            str(message.get("subject", "")),
            str(message.get("body_core", "")),
        )
        for message in messages
    ]
    numeric_scores = [
        value
        for review in reviews
        if isinstance((value := review.get("score")), (int, float))
    ]
    minimum_score = int(min(numeric_scores, default=0))
    approved = bool(reviews) and all(bool(review["approved"]) for review in reviews)
    return {
        "approved": approved,
        "minimum_score": minimum_score,
        "messages": reviews,
    }
