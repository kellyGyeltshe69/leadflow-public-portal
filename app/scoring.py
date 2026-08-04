from __future__ import annotations

from dataclasses import dataclass

HIGH_VALUE_TERMS = {
    "event venue", "wedding", "contractor", "roofing", "hvac", "plumber",
    "electrician", "dentist", "medical", "law", "attorney", "med spa",
}
MID_VALUE_TERMS = {
    "restaurant", "catering", "salon", "groomer", "auto detail", "cleaning",
    "bakery", "boutique", "cafe", "photographer",
}


@dataclass
class ScoreResult:
    need: float
    ability: float
    growth: float
    response: float
    weighted: float
    reasons: dict[str, str]


def clamp(value: float) -> float:
    return max(1.0, min(10.0, float(value)))


def weighted_score(need: float, ability: float, growth: float, response: float) -> float:
    return round(clamp(need) * 0.40 + clamp(ability) * 0.20 + clamp(growth) * 0.25 + clamp(response) * 0.15, 1)


def score_lead(
    website_status: str,
    issues: list[str],
    industry: str,
    has_public_email: bool,
    has_contact_page: bool,
    growth_signals: list[str] | None = None,
    facts: dict | None = None,
) -> ScoreResult:
    status = (website_status or "").lower()
    issue_text = " ".join(issues).lower()
    industry_text = (industry or "").lower()
    growth_signals = growth_signals or []
    facts = facts or {}

    need: float
    ability: float
    if "no_website" in status or "no independent" in status:
        need = 10
        need_reason = "No independent website was found in the verified public path."
    elif any(x in status for x in ["expired", "broken", "unreachable", "error"]):
        need = 10
        need_reason = "The first-party site was broken, expired or unreachable during the audit."
    elif any(x in issue_text for x in ["https", "viewport", "server response", "broken link"]):
        need = 8
        need_reason = "The site has one or more concrete technical/customer-journey gaps."
    elif issues:
        need = 6
        need_reason = "The site works but has observable content or conversion gaps."
    else:
        need = 3
        need_reason = "No major website problem was verified."

    technical_penalties = 0
    if facts.get("ssl_expired") is True or facts.get("https") is False:
        technical_penalties += 2
    scores = facts.get("scores") or {}
    if scores.get("performance", 100) < 60:
        technical_penalties += 1
    if scores.get("seo", 100) < 60:
        technical_penalties += 1
    if scores.get("mobile", 100) < 60:
        technical_penalties += 1
    need = clamp(max(need, min(10, 5 + technical_penalties)) if technical_penalties else need)

    if any(term in industry_text for term in HIGH_VALUE_TERMS):
        ability = 8
        ability_reason = "The public business model generally involves higher-value bookings or jobs; revenue was not verified."
    elif any(term in industry_text for term in MID_VALUE_TERMS):
        ability = 6
        ability_reason = "The business model suggests moderate website value; revenue was not verified."
    else:
        ability = 5
        ability_reason = "Ability to pay is uncertain and is not based on private financial data."

    review_count = int(facts.get("google_review_count") or 0)
    if review_count >= 100:
        ability = clamp(ability + 1)
        ability_reason += " Public review volume suggests an established operation, but revenue remains unverified."

    growth: float = 6 + min(3, len(growth_signals))
    if review_count >= 100:
        growth += 1
    if any("new" in s.lower() or "opening" in s.lower() for s in growth_signals):
        growth = max(growth, 8)
    growth = clamp(growth)
    growth_reason = "Growth score uses only public launch, expansion, activity or multi-location signals."

    if has_public_email:
        response = 8
        response_reason = "A public business email was found; deliverability is untested."
    elif has_contact_page:
        response = 5
        response_reason = "Only a public contact page was found; this system will not guess an email."
    else:
        response = 1
        response_reason = "No permitted public email/contact route was found."

    weighted = weighted_score(need, ability, growth, response)
    return ScoreResult(
        need=clamp(need), ability=clamp(ability), growth=growth, response=clamp(response), weighted=weighted,
        reasons={"need": need_reason, "ability": ability_reason, "growth": growth_reason, "response": response_reason},
    )
