import os
import json
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

OXYLABS_USERNAME = os.getenv("OXYLABS_USERNAME")
OXYLABS_PASSWORD = os.getenv("OXYLABS_PASSWORD")
OXYLABS_URL = "https://realtime.oxylabs.io/v1/queries"
GITHUB_BASE = "https://github.com"


def scrape_github(query: str, geo_location: str = None) -> dict:
    """
    Send a scrape request to the OxyLabs Realtime API targeting GitHub.

    Args:
        query: The GitHub URL to scrape.
        geo_location: Optional geo location for the request (e.g. '90210').

    Returns:
        Parsed JSON response from OxyLabs.
    """
    if not OXYLABS_USERNAME or not OXYLABS_PASSWORD:
        raise EnvironmentError(
            "OXYLABS_USERNAME and OXYLABS_PASSWORD must be set in your .env file."
        )

    payload = {
        "source": "universal",
        "url": query,
        "parse": False,
    }

    if geo_location:
        payload["geo_location"] = geo_location

    response = requests.post(
        OXYLABS_URL,
        auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
        json=payload,
    )
    response.raise_for_status()
    return response.json()


def enrich_repository(repo: dict) -> dict:
    """
    Scrape the individual repository page and its contributors fragment to fill in
    forks, followers (watchers), and contributor list.

    Mutates and returns the repo dict.
    """
    print(f"  Enriching {repo['name']}...")

    # --- Repo page: forks + watchers + contributor count ---
    repo_result = scrape_github(repo["url"])
    repo_html = repo_result["results"][0]["content"]
    repo_soup = BeautifulSoup(repo_html, "html.parser")

    for label, key in [("forks", "forks"), ("watchers", "followers")]:
        a = repo_soup.find("a", href=re.compile(f"/{label}$"))
        if a:
            match = re.search(r"[\d,]+", a.get_text(strip=True))
            repo[key] = int(match.group().replace(",", "")) if match else None

    # --- Contributors list fragment ---
    # Construct the URL directly from the repo path — more reliable than finding
    # the include-fragment element, which GitHub sometimes omits from the initial HTML.
    repo_path = repo["url"].replace(GITHUB_BASE, "")           # e.g. /owner/repo
    repo_name = repo_path.split("/")[-1]                        # e.g. repo
    contrib_url = (
        f"{GITHUB_BASE}{repo_path}/contributors_list"
        f"?current_repository={repo_name}&deferred=true"
    )
    contrib_result = scrape_github(contrib_url)
    contrib_html = contrib_result["results"][0]["content"]
    contrib_soup = BeautifulSoup(contrib_html, "html.parser")

    contributors = []
    for li in contrib_soup.select("ul li a[href]"):
        profile_url = li["href"]
        img = li.select_one("img")
        username = img["alt"].lstrip("@") if img and img.get("alt") else None
        if username:
            contributors.append({
                "username": username,
                "profile_url": profile_url,
            })

    # Counter span in the fragment carries the true total (may exceed avatars shown)
    counter = contrib_soup.select_one("span.Counter")
    repo["contributor_count"] = int(counter["title"]) if counter and counter.get("title") else len(contributors)
    repo["contributors"] = contributors
    return repo


def parse_repositories(html: str) -> dict:
    """
    Parse GitHub repository search results HTML into structured data.

    Returns a dict shaped as { "repo": { "repositories": [...] } }.
    Note: forks and followers are not rendered on the search results page;
    they are set to null here and would require individual repo/user API calls.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('div[class*="Result-module__Result"]')

    repositories = []
    for card in cards:
        # Name and URL
        link = card.select_one("div.search-title a")
        name = link.get_text(" ", strip=True).replace(" ", "") if link else None
        path = link["href"] if link else None
        url = f"{GITHUB_BASE}{path}" if path else None

        # Description
        desc_el = card.select_one('div[class*="Content-module__Content"] span')
        description = desc_el.get_text(strip=True) if desc_el else None

        # Stars — aria-label carries the exact count, e.g. "4821 stars"
        stars_link = card.select_one('a[aria-label*="stars"]')
        stars = None
        if stars_link:
            match = re.search(r"(\d[\d,]*)\s+stars", stars_link["aria-label"])
            stars = int(match.group(1).replace(",", "")) if match else None

        # Language
        lang_el = card.select_one('span[aria-label*="language"]')
        language = lang_el.get_text(strip=True) if lang_el else None

        repositories.append({
            "name": name,
            "description": description,
            "url": url,
            "stars": stars,
            "language": language,
            # Not available on search results page; fetch individual repo pages to populate
            "forks": None,
            "followers": None,
        })

    return {"repo": {"repositories": repositories}}


if __name__ == "__main__":
    # target_url = "https://github.com/search?q=location%3A%22San+Francisco%22+language%3APython&type=users"
    target_url = "https://github.com/search?q=open+claw&type=repositories&s=stars&o=desc"

    print(f"Scraping: {target_url}\n")
    result = scrape_github(query=target_url)

    html_content = result["results"][0]["content"]
    parsed = parse_repositories(html_content)

    print(f"Found {len(parsed['repo']['repositories'])} repositories. Enriching...\n")
    for repo in parsed["repo"]["repositories"]:
        enrich_repository(repo)

    output_path = "output.txt"
    with open(output_path, "w") as f:
        json.dump(parsed, f, indent=2)

    print(f"\nDone. Response written to {output_path}")
