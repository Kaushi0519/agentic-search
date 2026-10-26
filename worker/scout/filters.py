"""Undergrad and US-location filters."""

import re

from .models import Listing

# --- Degree level -----------------------------------------------------------

UNDERGRAD_DEGREES = {"Bachelor's", "Associate's"}

# "undergraduate" doesn't match \bgraduate\b, so it is kept.
GRAD_TITLE_RE = re.compile(
    r"\b(new grads?|new graduates?|recent graduates?|graduate|grad|"
    r"ph\.?\s?d|doctoral|doctorate|post-?doc|master'?s|mba|m\.?s\.?/ph\.?d)\b",
    re.IGNORECASE,
)


def is_undergrad(listing: Listing) -> bool:
    if GRAD_TITLE_RE.search(listing.title):
        return False
    # An empty or missing degree list means the source doesn't know; keep it.
    if listing.degrees and not UNDERGRAD_DEGREES.intersection(listing.degrees):
        return False
    return True


# --- Location ---------------------------------------------------------------

US_STATES = {
    "AL": "alabama", "AK": "alaska", "AZ": "arizona", "AR": "arkansas",
    "CA": "california", "CO": "colorado", "CT": "connecticut", "DE": "delaware",
    "FL": "florida", "GA": "georgia", "HI": "hawaii", "ID": "idaho",
    "IL": "illinois", "IN": "indiana", "IA": "iowa", "KS": "kansas",
    "KY": "kentucky", "LA": "louisiana", "ME": "maine", "MD": "maryland",
    "MA": "massachusetts", "MI": "michigan", "MN": "minnesota",
    "MS": "mississippi", "MO": "missouri", "MT": "montana", "NE": "nebraska",
    "NV": "nevada", "NH": "new hampshire", "NJ": "new jersey",
    "NM": "new mexico", "NY": "new york", "NC": "north carolina",
    "ND": "north dakota", "OH": "ohio", "OK": "oklahoma", "OR": "oregon",
    "PA": "pennsylvania", "RI": "rhode island", "SC": "south carolina",
    "SD": "south dakota", "TN": "tennessee", "TX": "texas", "UT": "utah",
    "VT": "vermont", "VA": "virginia", "WA": "washington",
    "WV": "west virginia", "WI": "wisconsin", "WY": "wyoming",
    "DC": "district of columbia", "PR": "puerto rico",
}

US_EXPLICIT_RE = re.compile(
    r"\b(united states|usa|u\.s\.a?\.?|remote in usa)\b|\bUS\b", re.IGNORECASE
)
# Case-sensitive so "in"/"or"/"me" in prose don't count as states.
US_STATE_CODE_RE = re.compile(
    r"(?:^|[\s,\-(])(" + "|".join(US_STATES) + r")(?=$|[\s,\-)])"
)
# "Carlsbad, Ca": a trailing two-letter code is a state whatever its case.
US_TRAILING_STATE_RE = re.compile(
    r",\s*(" + "|".join(US_STATES) + r")\s*$", re.IGNORECASE
)
US_STATE_NAME_RE = re.compile(
    r"\b(" + "|".join(US_STATES.values()) + r")\b", re.IGNORECASE
)
US_CITY_RE = re.compile(
    r"\b(nyc|sf|south sf|la|bay area|silicon valley|new york|san francisco|"
    r"los angeles|chicago|seattle|boston|austin|dallas|houston|atlanta|denver|"
    r"miami|philadelphia|pittsburgh|san jose|san diego|palo alto|mountain view|"
    r"sunnyvale|menlo park|redmond|bellevue|cupertino|santa clara|arlington|"
    r"jersey city|cambridge|detroit|minneapolis|phoenix|portland|raleigh|"
    r"durham|nashville|charlotte|salt lake city|columbus|baltimore|"
    r"st\.? louis|kansas city|orlando|tampa|irvine|oakland|brooklyn)\b",
    re.IGNORECASE,
)

CA_PROVINCE_RE = re.compile(r"(?:^|[\s,])(ON|BC|QC|AB|MB|NS|NB|SK|NL|PE)(?=$|[\s,])")
FOREIGN_COUNTRY_RE = re.compile(
    r"\b(canada|uk|u\.k\.|united kingdom|england|scotland|wales|ireland|"
    r"germany|france|netherlands|spain|italy|switzerland|sweden|denmark|norway|"
    r"finland|poland|portugal|belgium|austria|czech|romania|hungary|israel|"
    r"india|china|japan|korea|singapore|hong kong|taiwan|australia|"
    r"new zealand|brazil|(?<!new )mexico|argentina|chile|colombia|philippines|"
    r"vietnam|thailand|malaysia|indonesia|uae|united arab emirates|dubai|abu dhabi|"
    r"qatar|saudi|egypt|nigeria|"
    r"south africa|kenya|turkey|greece|luxembourg|estonia)\b",
    re.IGNORECASE,
)
FOREIGN_CITY_RE = re.compile(
    r"\b(toronto|vancouver|montr[eé]al|ottawa|waterloo|calgary|edmonton|"
    r"mississauga|markham|london|dublin|paris|berlin|munich|amsterdam|zurich|"
    r"stockholm|tel aviv|bangalore|bengaluru|hyderabad|mumbai|pune|delhi|"
    r"tokyo|seoul|shanghai|beijing|shenzhen|sydney|melbourne)\b",
    re.IGNORECASE,
)


def classify_location(loc: str) -> str:
    """Return "us", "foreign" or "unknown" for one location string."""
    s = loc.strip()
    if not s:
        return "unknown"
    if US_EXPLICIT_RE.search(s):
        return "us"
    if FOREIGN_COUNTRY_RE.search(s) or CA_PROVINCE_RE.search(s):
        return "foreign"
    if (US_STATE_CODE_RE.search(s) or US_TRAILING_STATE_RE.search(s)
            or US_STATE_NAME_RE.search(s) or US_CITY_RE.search(s)):
        return "us"
    if FOREIGN_CITY_RE.search(s):
        return "foreign"
    return "unknown"


def us_locations(locations: list[str]) -> list[str] | None:
    """Locations to show for a US role, or None if the role isn't in the US.

    Unknown locations (e.g. "Remote") are kept: missing a real role is worse
    than seeing one that turns out not to be US.
    """
    if not locations:
        return []
    kept = [loc for loc in locations if classify_location(loc) != "foreign"]
    return kept or None
