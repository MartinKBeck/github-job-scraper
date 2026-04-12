import argparse
import base64
import os
import json
import re
from pathlib import Path
from urllib.parse import quote_plus
import requests
from bs4 import BeautifulSoup
from dotenv import load_dotenv

load_dotenv()

OXYLABS_USERNAME = os.getenv("OXYLABS_USERNAME")
OXYLABS_PASSWORD = os.getenv("OXYLABS_PASSWORD")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")          # optional — raises API rate limit to 5k/hr
OXYLABS_URL = "https://realtime.oxylabs.io/v1/queries"
GITHUB_BASE = "https://github.com"
GITHUB_API = "https://api.github.com"


def scrape_github(url: str, geo_location: str = None, render: bool = False) -> dict:
    """Send a scrape request to the OxyLabs Realtime API."""
    if not OXYLABS_USERNAME or not OXYLABS_PASSWORD:
        raise EnvironmentError(
            "OXYLABS_USERNAME and OXYLABS_PASSWORD must be set in your .env file."
        )

    payload = {"source": "universal", "url": url, "parse": False}
    if geo_location:
        payload["geo_location"] = geo_location
    if render:
        payload["render"] = "html"

    response = requests.post(
        OXYLABS_URL,
        auth=(OXYLABS_USERNAME, OXYLABS_PASSWORD),
        json=payload,
    )
    response.raise_for_status()
    return response.json()


def fetch_commit_counts(owner: str, repo: str) -> dict:
    """
    Fetch per-contributor commit counts from the GitHub REST API.
    Returns a dict mapping lowercase username -> commit count.
    Uses GITHUB_TOKEN if set; falls back to unauthenticated (60 req/hr limit).
    """
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    counts = {}
    page = 1
    while True:
        resp = requests.get(
            f"{GITHUB_API}/repos/{owner}/{repo}/contributors",
            headers=headers,
            params={"per_page": 100, "page": page},
        )
        if resp.status_code != 200:
            break
        data = resp.json()
        if not data:
            break
        for entry in data:
            counts[entry["login"].lower()] = entry["contributions"]
        page += 1

    return counts


def fetch_profile_readme(username: str):
    """
    Fetch the GitHub profile README from the special username/username repo.
    Returns decoded text content, or None if no profile README exists.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    resp = requests.get(
        f"{GITHUB_API}/repos/{username}/{username}/readme",
        headers=headers,
    )
    if resp.status_code != 200:
        return None

    data = resp.json()
    content = data.get("content", "")
    if data.get("encoding") == "base64":
        content = base64.b64decode(content).decode("utf-8", errors="replace")

    # Strip HTML comments (GitHub's boilerplate template lives in these)
    content = re.sub(r"<!--.*?-->", "", content, flags=re.DOTALL).strip()
    return content if content else None


def fetch_user_email(username: str):
    """
    Fetch public email for a GitHub user via the REST API.
    More reliable than scraping — returns None if no public email is set.
    """
    headers = {"Accept": "application/vnd.github+json"}
    if GITHUB_TOKEN:
        headers["Authorization"] = f"Bearer {GITHUB_TOKEN}"

    resp = requests.get(f"{GITHUB_API}/users/{username}", headers=headers)
    if resp.status_code != 200:
        return None
    return resp.json().get("email") or None


def scrape_contributor_profile(profile_url: str) -> dict:
    """
    Scrape a GitHub user profile page and return:
      bio, email, website, pinned_repositories
    """
    username = profile_url.rstrip("/").split("/")[-1]

    result = scrape_github(profile_url, render=True)
    soup = BeautifulSoup(result["results"][0]["content"], "html.parser")

    # Full name
    name_el = soup.select_one("span[itemprop='name']")
    name = name_el.get_text(strip=True) if name_el else None

    # Bio
    bio_el = soup.select_one("[data-bio-text]")
    bio = bio_el.get_text(strip=True) if bio_el else None

    # Email — regex scan of JS-rendered HTML, fall back to GitHub API
    raw_html = result["results"][0]["content"]
    email_match = re.search(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+[a-zA-Z0-9]", raw_html)
    email = email_match.group(0) if email_match else fetch_user_email(username)

    # Website (first vcard-detail link that isn't GitHub itself)
    website = None
    for li in soup.select("li.vcard-detail"):
        a = li.select_one("a[href^='http']")
        if a and "github.com" not in a["href"]:
            website = a["href"]
            break

    # Pinned repositories
    pinned = []
    for card in soup.select("div.pinned-item-list-item-content")[:4]:
        a = card.select_one("a[href]")
        desc_el = card.select_one('[class*="pinned-item-desc"]')
        stars_el = card.select_one('a[href*="/stargazers"]')
        lang_el = card.select_one('span[itemprop="programmingLanguage"]')
        if a:
            pinned.append({
                "name": a.get_text(strip=True),
                "url": GITHUB_BASE + a["href"] if not a["href"].startswith("http") else a["href"],
                "description": desc_el.get_text(strip=True) if desc_el else None,
                "stars": stars_el.get_text(strip=True) if stars_el else None,
                "language": lang_el.get_text(strip=True) if lang_el else None,
            })

    return {
        "name": name,
        "bio": bio,
        "extended_bio": fetch_profile_readme(username),
        "email": email,
        "website": website,
        "pinned_repositories": pinned,
    }


def enrich_repository(repo: dict) -> dict:
    """
    Scrape the individual repository page and its contributors fragment to fill in
    forks, followers (watchers), contributor list, commit counts, and profile details.
    Mutates and returns the repo dict.
    """
    print(f"  Enriching {repo['name']}...")

    # --- Repo page: forks + watchers ---
    repo_html = scrape_github(repo["url"])["results"][0]["content"]
    repo_soup = BeautifulSoup(repo_html, "html.parser")

    for label, key in [("forks", "forks"), ("watchers", "followers")]:
        a = repo_soup.find("a", href=re.compile(f"/{label}$"))
        if a:
            match = re.search(r"[\d,]+", a.get_text(strip=True))
            repo[key] = int(match.group().replace(",", "")) if match else None

    # --- Commit counts via GitHub API (one call covers all contributors) ---
    owner, repo_name = repo["name"].split("/", 1)
    commit_counts = fetch_commit_counts(owner, repo_name)

    # --- Contributors list fragment ---
    repo_path = f"/{owner}/{repo_name}"
    contrib_url = (
        f"{GITHUB_BASE}{repo_path}/contributors_list"
        f"?current_repository={repo_name}&deferred=true"
    )
    contrib_html = scrape_github(contrib_url)["results"][0]["content"]
    contrib_soup = BeautifulSoup(contrib_html, "html.parser")

    counter = contrib_soup.select_one("span.Counter")
    repo["contributor_count"] = (
        int(counter["title"].replace(",", "")) if counter and counter.get("title") else None
    )

    NON_HUMAN = {
        "github-actions", "dependabot", "renovate", "renovate-bot",
        "allcontributors", "github-copilot", "githubbcopilot", "copilot",
        "claude", "pre-commit-ci", "imgbot", "snyk-bot", "codecov",
        "semantic-release-bot", "github-advanced-security",
    }

    contributors = []
    for li in contrib_soup.select("ul li a[href]"):
        img = li.select_one("img")
        username = img["alt"].lstrip("@") if img and img.get("alt") else None
        if not username:
            continue
        if username.lower().endswith("[bot]") or username.lower() in NON_HUMAN:
            print(f"    Skipping non-human: {username}")
            continue

        print(f"    Scraping profile: {username}")
        profile = scrape_contributor_profile(li["href"])

        contributors.append({
            "name": profile.pop("name", None),
            "username": username,
            "profile_url": li["href"],
            "commits_to_repo": commit_counts.get(username.lower()),
            **profile,
        })

    repo["contributors"] = contributors
    return repo


def parse_repositories(html: str) -> dict:
    """
    Parse GitHub repository search results HTML into structured data.
    Returns { "repo": { "repositories": [...] } }.
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

    return {"repositories": repositories}


SEARCH_CONFIG_PATH = "search_config.json"


def load_search_config(path: str) -> dict | None:
    """Load search_config.json produced by determine_search.py, if it exists."""
    if not Path(path).exists():
        return None
    with open(path) as f:
        return json.load(f)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Scrape GitHub repository search results.")
    parser.add_argument(
        "query",
        nargs="?",
        default=None,
        help="Search term(s) to query on GitHub. If omitted, reads from search_config.json.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        metavar="N",
        help="Only enrich the first N repositories (useful for testing)",
    )
    parser.add_argument(
        "--config",
        default=SEARCH_CONFIG_PATH,
        help=f"Path to search config JSON from determine_search.py (default: {SEARCH_CONFIG_PATH})",
    )
    args = parser.parse_args()

    # --- Resolve search queries: CLI arg, search_config.json, or default ---
    search_config = load_search_config(args.config)
    role_context = None

    if args.query:
        # Explicit CLI query takes priority
        queries = [{"query": args.query, "description": "CLI argument"}]
    elif search_config:
        queries = search_config.get("search_queries", [])
        role_context = search_config.get("role_context")
        strategy = search_config.get("strategy", "skill")
        print(f"Loaded search config ({strategy} strategy) with {len(queries)} queries.")
        if role_context:
            print(f"  Role: {role_context.get('title', 'N/A')}")
            print(f"  Profile: {role_context.get('description', 'N/A')}")
        print()
    else:
        # Fallback to the original default
        queries = [{"query": "open claw", "description": "Default search"}]

    if not queries:
        print("No search queries found. Provide a query or run determine_search.py first.")
        raise SystemExit(1)

    # --- Run all search queries and aggregate results ---
    all_repos = []
    for sq in queries:
        q = sq["query"]
        target_url = (
            f"https://github.com/search?q={quote_plus(q)}"
            f"&type=repositories&s=stars&o=desc"
        )

        print(f"Scraping: {target_url}")
        print(f"  ({sq.get('description', '')})")
        result = scrape_github(target_url)

        html_content = result["results"][0]["content"]
        parsed = parse_repositories(html_content)
        found = parsed["repositories"]
        print(f"  Found {len(found)} repositories.\n")
        all_repos.extend(found)

    # Deduplicate by repo name (keep first occurrence)
    seen = set()
    unique_repos = []
    for repo in all_repos:
        if repo["name"] and repo["name"] not in seen:
            seen.add(repo["name"])
            unique_repos.append(repo)

    repos = unique_repos
    if args.limit:
        repos = repos[: args.limit]

    print(f"Total unique repositories: {len(unique_repos)}. Enriching {len(repos)}...\n")
    for repo in repos:
        enrich_repository(repo)

    # Build output with role_context for downstream pipeline steps
    output_data = {"repositories": repos}
    if role_context:
        output_data["role_context"] = role_context

    output_path = "output.txt"
    with open(output_path, "w") as f:
        json.dump(output_data, f, indent=2)

    print(f"\nDone. Response written to {output_path}")
    if role_context:
        print(f"  Role context included for downstream pipeline steps.")
