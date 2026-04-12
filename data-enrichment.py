# data-enrichment.py
#
# This module handles data enrichment from sources outside of GitHub.
# Once the GitHub scraper (oxylabs-scraper.py) has produced output.txt,
# pass that data through here to layer in additional signals from
# professional networks and other platforms — e.g. LinkedIn via Enrich Layer,
# Twitter/X, personal websites, company lookup, etc.
#
# Intended flow:
#   1. Run oxylabs-scraper.py  →  output.txt
#   2. Run data-enrichment.py  →  enriched_output.txt

import json
import os
from datetime import date
import anthropic
import requests
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
ENRICHLAYER_API_KEY = os.getenv("ENRICHLAYER_API_KEY")
OXYLABS_USERNAME = os.getenv("OXYLABS_USERNAME")
OXYLABS_PASSWORD = os.getenv("OXYLABS_PASSWORD")
OXYLABS_URL = "https://realtime.oxylabs.io/v1/queries"

INPUT_PATH = "output.txt"
OUTPUT_PATH = "enriched_output.txt"


def scrape_url(url: str) -> str:
    """Scrape an arbitrary URL via OxyLabs and return the raw HTML."""
    if not OXYLABS_USERNAME or not OXYLABS_PASSWORD:
        raise EnvironmentError(
            "OXYLABS_USERNAME and OXYLABS_PASSWORD must be set in your .env file."
        )
    payload = {"source": "universal", "url": url, "render": "html", "parse": False}
    response = requests.post(
        OXYLABS_URL,
        auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
        json=payload,
    )
    response.raise_for_status()
    return response.json()["results"][0]["content"]


# ---------------------------------------------------------------------------
# Enrichment functions
# Add one function per data source. Each function receives a contributor dict
# and returns it with additional fields merged in.
# ---------------------------------------------------------------------------

def _format_tenure(starts_at: dict) -> str:
    """Convert an Enrich Layer starts_at dict to a human-readable tenure string."""
    if not starts_at:
        return None
    year = starts_at.get("year")
    month = starts_at.get("month", 1)
    if not year:
        return None
    start = date(year, month, 1)
    today = date.today()
    total_months = (today.year - start.year) * 12 + (today.month - start.month)
    years, rem = divmod(total_months, 12)
    if years and rem:
        return f"{years} yr{'s' if years > 1 else ''} {rem} mo{'s' if rem > 1 else ''}"
    if years:
        return f"{years} yr{'s' if years > 1 else ''}"
    return f"{rem} mo{'s' if rem > 1 else ''}"


def enrich_from_linkedin(contributor: dict) -> dict:
    """
    Resolve and enrich a LinkedIn profile using the Enrich Layer API.
    Adds linkedin_profile_url and linkedin_profile_data (current role, company,
    time_at_role) to the contributor dict.
    """
    if not ENRICHLAYER_API_KEY:
        raise EnvironmentError("ENRICHLAYER_API_KEY must be set in your .env file.")

    name = (contributor.get("name") or "").strip()
    parts = name.split()
    if len(parts) < 2:
        print(f"      Skipping Enrich Layer — no full name available")
        return contributor

    first_name = parts[0]
    last_name = parts[-1]

    params = {
        "first_name": first_name,
        "last_name": last_name,
        "similarity_checks": "include",
        "enrich_profile": "enrich",
    }

    # Pass optional context to improve match accuracy
    if contributor.get("bio"):
        params["title"] = contributor["bio"][:120]

    print(f"      Enrich Layer lookup: {first_name} {last_name}")
    resp = requests.get(
        "https://enrichlayer.com/api/v2/profile/resolve",
        headers={"Authorization": ENRICHLAYER_API_KEY},
        params=params,
    )

    if resp.status_code != 200:
        print(f"      Enrich Layer returned {resp.status_code}")
        return contributor

    data = resp.json()

    contributor["linkedin_profile_url"] = data.get("linkedin_url") or data.get("url")

    experiences = data.get("experiences") or []
    if experiences:
        current = experiences[0]
        contributor["linkedin_profile_data"] = {
            "current_role": current.get("title"),
            "current_company": current.get("company"),
            "time_at_role": _format_tenure(current.get("starts_at")),
        }

    return contributor


def enrich_contributor(contributor: dict) -> dict:
    """Run all enrichment steps for a single contributor."""
    contributor = enrich_from_linkedin(contributor)
    # Add further enrichment calls here as new sources are integrated
    return contributor


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    with open(INPUT_PATH) as f:
        data = json.load(f)

    for repo in data.get("repositories", []):
        for i, contributor in enumerate(repo.get("contributors", [])):
            print(f"  Enriching {contributor.get('username', '?')}...")
            repo["contributors"][i] = enrich_contributor(contributor)

    with open(OUTPUT_PATH, "w") as f:
        json.dump(data, f, indent=2)

    print(f"\nDone. Enriched data written to {OUTPUT_PATH}")
