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
import re
from datetime import date
from urllib.parse import quote_plus
import anthropic
import requests
from bs4 import BeautifulSoup
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
# Claude-powered professional info extraction
# ---------------------------------------------------------------------------

def extract_professional_info(contributor: dict) -> dict:
    """
    Use Claude to extract structured professional info (job title, company,
    company domain, location) from a contributor's GitHub bio and extended bio.
    Returns a dict with keys: title, company, company_domain, location.
    """
    if not ANTHROPIC_API_KEY:
        print("      Skipping Claude extraction — ANTHROPIC_API_KEY not set")
        return {}

    bio = contributor.get("bio") or ""
    extended_bio = contributor.get("extended_bio") or ""
    name = contributor.get("name") or ""
    username = contributor.get("username") or ""
    website = contributor.get("website") or ""

    context_parts = []
    if name:
        context_parts.append(f"Name: {name}")
    if username:
        context_parts.append(f"GitHub username: {username}")
    if bio:
        context_parts.append(f"GitHub bio: {bio}")
    if extended_bio:
        context_parts.append(f"GitHub profile README:\n{extended_bio[:1500]}")
    if website:
        context_parts.append(f"Website: {website}")

    if not bio and not extended_bio:
        return {}

    context = "\n".join(context_parts)
    # Sanitize surrogate characters that can appear in emoji-rich GitHub bios
    context = context.encode("utf-8", errors="replace").decode("utf-8")

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=300,
        messages=[
            {
                "role": "user",
                "content": (
                    "Extract the following professional details from this GitHub profile. "
                    "Return ONLY valid JSON with these keys: "
                    '"title" (current job title, e.g. "Research Scientist", "PhD Student", "Software Engineer"), '
                    '"company" (current employer or university name), '
                    '"company_domain" (company website domain, e.g. "google.com", "princeton.edu"), '
                    '"location" (city, state, or country if mentioned). '
                    "Use null for any field you cannot determine. "
                    "Do not guess or make up values — only extract what is explicitly stated.\n\n"
                    f"{context}"
                ),
            }
        ],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        info = json.loads(raw)
        print(f"      Claude extracted: {info}")
        return info
    except json.JSONDecodeError:
        print(f"      Claude returned non-JSON: {raw[:200]}")
        return {}


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


def _apply_enrichlayer_data(contributor: dict, data: dict) -> dict:
    """Apply Enrich Layer response data to the contributor dict."""
    linkedin_url = data.get("linkedin_url") or data.get("url")
    if linkedin_url:
        # Normalize relative URLs from Enrich Layer
        if linkedin_url.startswith("/"):
            linkedin_url = f"https://www.linkedin.com{linkedin_url}"
        elif not linkedin_url.startswith("http"):
            linkedin_url = f"https://www.linkedin.com/in/{linkedin_url}"
        contributor["linkedin_profile_url"] = linkedin_url

    # Profile data may be nested under "profile" key when enrich_profile=enrich
    profile = data.get("profile") or data
    experiences = profile.get("experiences") or []
    if experiences:
        current = experiences[0]
        contributor["linkedin_profile_data"] = {
            "current_role": current.get("title"),
            "current_company": current.get("company"),
            "time_at_role": _format_tenure(current.get("starts_at")),
        }

    return contributor


def _enrichlayer_resolve(first_name: str, last_name: str, pro_info: dict) -> dict:
    """
    Call the Enrich Layer Person Lookup endpoint with structured params.
    Returns the API response dict, or None on failure.
    """
    company_domain = pro_info.get("company_domain") or ""
    if not company_domain:
        return None

    params = {
        "first_name": first_name,
        "last_name": last_name,
        "company_domain": company_domain,
        "similarity_checks": "include",
        "enrich_profile": "enrich",
    }
    if pro_info.get("title"):
        params["title"] = pro_info["title"]
    if pro_info.get("location"):
        params["location"] = pro_info["location"]

    print(f"      Enrich Layer lookup: {first_name} {last_name} @ {company_domain}")
    resp = requests.get(
        "https://enrichlayer.com/api/v2/profile/resolve",
        headers={"Authorization": f"Bearer {ENRICHLAYER_API_KEY}"},
        params=params,
    )

    if resp.status_code != 200:
        print(f"      Enrich Layer returned {resp.status_code}")
        return None

    data = resp.json()
    if not data or data.get("url") is None:
        print("      Enrich Layer returned no match")
        return None

    return data


def _enrichlayer_email_lookup(email: str) -> dict:
    """
    Call the Enrich Layer Reverse Email Lookup endpoint.
    Returns the API response dict, or None on failure.
    """
    if not email or not ENRICHLAYER_API_KEY:
        return None

    params = {
        "email": email,
        "lookup_depth": "deep",
        "enrich_profile": "enrich",
    }

    print(f"      Enrich Layer email lookup: {email}")
    resp = requests.get(
        "https://enrichlayer.com/api/v2/profile/resolve/email",
        headers={"Authorization": f"Bearer {ENRICHLAYER_API_KEY}"},
        params=params,
    )

    if resp.status_code != 200:
        print(f"      Enrich Layer email lookup returned {resp.status_code}")
        return None

    data = resp.json()
    if not data or data.get("url") is None:
        print("      Enrich Layer email lookup returned no match")
        return None

    return data


def _google_linkedin_search(name: str, pro_info: dict) -> str:
    """
    Search Google via OxyLabs for a LinkedIn profile URL.
    Returns the first linkedin.com/in/ URL found, or None.
    """
    if not OXYLABS_USERNAME or not OXYLABS_PASSWORD:
        return None

    query_parts = [f'site:linkedin.com/in "{name}"']
    if pro_info.get("company"):
        query_parts.append(f'"{pro_info["company"]}"')
    elif pro_info.get("title"):
        query_parts.append(f'"{pro_info["title"]}"')

    query = " ".join(query_parts)
    search_url = f"https://www.google.com/search?q={quote_plus(query)}"

    print(f"      Google fallback: {query}")
    try:
        html = scrape_url(search_url)
    except Exception as e:
        print(f"      Google search failed: {e}")
        return None

    soup = BeautifulSoup(html, "html.parser")

    linkedin_pattern = re.compile(
        r"https?://(?:www\.)?linkedin\.com/in/([a-zA-Z0-9_-]+)"
    )

    # Check all anchor tags first
    for a in soup.find_all("a", href=True):
        match = linkedin_pattern.search(a["href"])
        if match:
            return f"https://www.linkedin.com/in/{match.group(1)}"

    # Check cite elements (Google shows URLs in <cite> tags)
    for cite in soup.find_all("cite"):
        cite_text = cite.get_text()
        match = linkedin_pattern.search(cite_text)
        if match:
            return f"https://www.linkedin.com/in/{match.group(1)}"

    # Fall back to scanning raw HTML text for LinkedIn URLs
    match = linkedin_pattern.search(soup.get_text())
    if match:
        return f"https://www.linkedin.com/in/{match.group(1)}"

    print("      No LinkedIn URL found in Google results")
    return None


def enrich_from_linkedin(contributor: dict) -> dict:
    """
    Resolve and enrich a LinkedIn profile using multiple strategies:
      1. Enrich Layer email lookup (if public email available)
      2. Enrich Layer name+company lookup (using Claude-extracted professional info)
      3. Google site-search fallback via OxyLabs
    Adds linkedin_profile_url and linkedin_profile_data to the contributor dict.
    """
    # --- Strategy 1: Email-based reverse lookup (no name required) ---
    email = contributor.get("email")
    if email and ENRICHLAYER_API_KEY:
        data = _enrichlayer_email_lookup(email)
        if data:
            print("      Matched via email lookup")
            return _apply_enrichlayer_data(contributor, data)

    # Strategies 2 & 3 require at least a two-part name
    name = (contributor.get("name") or "").strip()
    parts = name.split()
    if len(parts) < 2:
        print("      Skipping remaining strategies — no full name available")
        return contributor

    first_name = parts[0]
    last_name = parts[-1]

    # --- Extract professional info using Claude ---
    pro_info = extract_professional_info(contributor)

    # --- Strategy 2: Enrich Layer name + company lookup ---
    if ENRICHLAYER_API_KEY and pro_info.get("company_domain"):
        data = _enrichlayer_resolve(first_name, last_name, pro_info)
        if data:
            print("      Matched via Enrich Layer name+company lookup")
            return _apply_enrichlayer_data(contributor, data)

    # --- Strategy 3: Google site-search fallback ---
    linkedin_url = _google_linkedin_search(name, pro_info)
    if linkedin_url:
        contributor["linkedin_profile_url"] = linkedin_url
        print(f"      Matched via Google search: {linkedin_url}")

        # If we have an Enrich Layer key, try to enrich the found profile
        if ENRICHLAYER_API_KEY:
            try:
                print("      Enriching found profile via Enrich Layer...")
                resp = requests.get(
                    "https://enrichlayer.com/api/v2/profile",
                    headers={"Authorization": f"Bearer {ENRICHLAYER_API_KEY}"},
                    params={
                        "profile_url": linkedin_url,
                        "use_cache": "if-present",
                    },
                )
                if resp.status_code == 200:
                    profile_data = resp.json()
                    experiences = profile_data.get("experiences") or []
                    if experiences:
                        current = experiences[0]
                        contributor["linkedin_profile_data"] = {
                            "current_role": current.get("title"),
                            "current_company": current.get("company"),
                            "time_at_role": _format_tenure(current.get("starts_at")),
                        }
            except Exception as e:
                print(f"      Profile enrichment failed: {e}")

        return contributor

    print("      No LinkedIn profile found via any strategy")
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
