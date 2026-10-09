"""Rough source-credibility tiers by domain.

This is deliberately simple and transparent: it is shown to the user as a hint,
never used to hide a source. Tiers feed the ordering of the source list and the
verdict prompt (so the writer prefers official and reference sources).
"""

from __future__ import annotations

TIERS = {
    "official": ("Official", 1.0),
    "reference": ("Reference", 0.85),
    "news": ("News", 0.7),
    "web": ("Web", 0.5),
    "community": ("Community", 0.35),
}

_OFFICIAL_SUFFIXES = (
    ".gov", ".gov.in", ".nic.in", ".gov.uk", ".gov.au", ".gc.ca", ".mil", ".edu", ".ac.in", ".ac.uk",
    ".edu.in", ".res.in", ".int",
)
_OFFICIAL_DOMAINS = {
    "who.int", "un.org", "unicef.org", "europa.eu", "rbi.org.in", "sebi.gov.in", "india.gov.in",
    "mygov.in", "icmr.gov.in", "uidai.gov.in", "incometax.gov.in", "pib.gov.in", "worldbank.org", "imf.org",
}
_REFERENCE_DOMAINS = {
    "wikipedia.org", "britannica.com", "mayoclinic.org", "clevelandclinic.org", "nhs.uk", "webmd.com",
    "medlineplus.gov", "nature.com", "science.org", "sciencedirect.com", "springer.com", "thelancet.com",
    "bmj.com", "jamanetwork.com", "nejm.org", "cochranelibrary.com", "khanacademy.org", "investopedia.com",
    "merriam-webster.com", "oxfordreference.com", "nasa.gov", "drugs.com", "healthline.com",
}
_NEWS_DOMAINS = {
    "reuters.com", "apnews.com", "bbc.com", "bbc.co.uk", "thehindu.com", "indianexpress.com",
    "hindustantimes.com", "ndtv.com", "timesofindia.indiatimes.com", "indiatimes.com", "livemint.com",
    "economictimes.indiatimes.com", "business-standard.com", "theprint.in", "scroll.in", "thewire.in",
    "nytimes.com", "theguardian.com", "washingtonpost.com", "aljazeera.com", "cnn.com", "npr.org",
    "moneycontrol.com", "news18.com", "indiatoday.in", "deccanherald.com", "downtoearth.org.in",
}
_COMMUNITY_DOMAINS = {
    "reddit.com", "quora.com", "stackoverflow.com", "stackexchange.com", "youtube.com", "medium.com",
    "facebook.com", "x.com", "twitter.com", "instagram.com", "linkedin.com", "tiktok.com", "pinterest.com",
    "tumblr.com", "blogspot.com", "wordpress.com", "substack.com",
}


def _matches(domain: str, names: set[str]) -> bool:
    return any(domain == name or domain.endswith("." + name) for name in names)


def tier_of(domain: str) -> str:
    domain = (domain or "").lower()
    if not domain:
        return "web"
    if domain.endswith(_OFFICIAL_SUFFIXES) or _matches(domain, _OFFICIAL_DOMAINS):
        return "official"
    if _matches(domain, _REFERENCE_DOMAINS):
        return "reference"
    if _matches(domain, _NEWS_DOMAINS):
        return "news"
    if _matches(domain, _COMMUNITY_DOMAINS) or "forum" in domain:
        return "community"
    return "web"


def describe(domain: str) -> dict[str, object]:
    tier = tier_of(domain)
    label, score = TIERS[tier]
    return {"tier": tier, "tier_label": label, "tier_score": score}
