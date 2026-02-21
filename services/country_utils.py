"""Utilities for normalising country data from the WarEra API."""


def extract_country_list(api_response) -> list[dict]:
    """Normalise the getAllCountries API envelope into a plain list of country dicts."""
    if isinstance(api_response, list):
        return [c for c in api_response if isinstance(c, dict)]
    if isinstance(api_response, dict):
        if isinstance(api_response.get("data"), list):
            return [c for c in api_response["data"] if isinstance(c, dict)]
        r = api_response.get("result")
        if isinstance(r, dict) and isinstance(r.get("data"), list):
            return [c for c in r["data"] if isinstance(c, dict)]
        for key in ("countries", "items"):
            v = api_response.get(key)
            if isinstance(v, list):
                return [c for c in v if isinstance(c, dict)]
    return []


def find_country(query: str, country_list: list[dict]) -> dict | None:
    """Find a country by code or name (case-insensitive).

    Matching priority:
      1. Exact code match  (e.g. "NL", "CH")
      2. Exact name match  (e.g. "Netherlands", "Switzerland")
      3. Name starts-with  (e.g. "switz" → Switzerland)
    """
    q = query.strip().lower()
    hit = next((c for c in country_list if str(c.get("code", "")).lower() == q), None)
    if hit:
        return hit
    hit = next((c for c in country_list if str(c.get("name", "")).lower() == q), None)
    if hit:
        return hit
    return next((c for c in country_list if str(c.get("name", "")).lower().startswith(q)), None)


def country_id(country: dict) -> str:
    """Return the best available ID for a country dict."""
    return str(
        country.get("_id")
        or country.get("id")
        or country.get("countryId")
        or country.get("code")
    )
