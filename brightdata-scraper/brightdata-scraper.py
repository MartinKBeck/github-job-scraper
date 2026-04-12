import os
import json
import re
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

BRIGHTDATA_CUSTOMER_ID = os.getenv("BRIGHTDATA_CUSTOMER_ID")
BRIGHTDATA_ZONE = os.getenv("BRIGHTDATA_ZONE", "web_unlocker1")
BRIGHTDATA_PASSWORD = os.getenv("BRIGHTDATA_PASSWORD")
GITHUB_BASE = "https://github.com"


def scrape_github(query: str, geo_location: str = None) -> dict:
    """
    Send a scrape request through Bright Data's Web Unlocker targeting GitHub.

    Args:
        query: The GitHub URL to scrape.
        geo_location: Optional two-letter country code (e.g. 'us').

    Returns:
        Dict with a 'results' key containing the page content, matching
        the shape used by the oxylabs scraper for interoperability.
    """
    if not BRIGHTDATA_CUSTOMER_ID or not BRIGHTDATA_PASSWORD:
        raise EnvironmentError(
            "BRIGHTDATA_CUSTOMER_ID and BRIGHTDATA_PASSWORD must be set in your .env file."
        )

    proxy_user = f"brd-customer-{BRIGHTDATA_CUSTOMER_ID}-zone-{BRIGHTDATA_ZONE}"
    if geo_location:
        proxy_user += f"-country-{geo_location}"

    proxies = {
        "http": f"http://{proxy_user}:{BRIGHTDATA_PASSWORD}@brd.superproxy.io:33335",
        "https": f"http://{proxy_user}:{BRIGHTDATA_PASSWORD}@brd.superproxy.io:33335",
    }

    response = requests.get(
        query,
        proxies=proxies,
        verify=False,
    )
    response.raise_for_status()

    return {"results": [{"content": response.text}]}


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
    repo_path = repo["url"].replace(GITHUB_BASE, "")
    repo_name = repo_path.split("/")[-1]
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

    counter = contrib_soup.select_one("span.Counter")
    repo["contributor_count"] = int(counter["title"]) if counter and counter.get("title") else len(contributors)
    repo["contributors"] = contributors
    return repo


def parse_repositories(html: str) -> dict:
    """
    Parse GitHub repository search results HTML into structured data.

    Returns a dict shaped as { "repo": { "repositories": [...] } }.
    """
    soup = BeautifulSoup(html, "html.parser")
    cards = soup.select('div[class*="Result-module__Result"]')

    repositories = []
    for card in cards:
        link = card.select_one("div.search-title a")
        name = link.get_text(" ", strip=True).replace(" ", "") if link else None
        path = link["href"] if link else None
        url = f"{GITHUB_BASE}{path}" if path else None

        desc_el = card.select_one('div[class*="Content-module__Content"] span')
        description = desc_el.get_text(strip=True) if desc_el else None

        stars_link = card.select_one('a[aria-label*="stars"]')
        stars = None
        if stars_link:
            match = re.search(r"(\d[\d,]*)\s+stars", stars_link["aria-label"])
            stars = int(match.group(1).replace(",", "")) if match else None

        lang_el = card.select_one('span[aria-label*="language"]')
        language = lang_el.get_text(strip=True) if lang_el else None

        repositories.append({
            "name": name,
            "description": description,
            "url": url,
            "stars": stars,
            "language": language,
            "forks": None,
            "followers": None,
        })

    return {"repo": {"repositories": repositories}}


if __name__ == "__main__":
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
