# Testing the GitHub Job Scraper Pipeline

## Overview
The pipeline has 5 sequential steps. Each step produces output consumed by the next.

## Devin Secrets Needed
- `ANTHROPIC_API_KEY` — Claude API for determine_search.py, profile_contributors.py, rank_contributors.py
- `OXYLABS_USERNAME` / `OXYLABS_PASSWORD` — Web scraping proxy for oxylabs-scraper.py and data-enrichment.py
- `GITHUB_TOKEN` — GitHub API for commit counts in oxylabs-scraper.py
- `ENRICHLAYER_API_KEY` — LinkedIn enrichment in data-enrichment.py

All secrets should be in the `.env` file. Source it before running: `source .env`

## Environment Setup
```bash
source .venv/bin/activate
source .env
```

## Pipeline Execution Order

### Step 1: determine_search.py
```bash
python determine_search.py "<hiring request>"
```
- Outputs `search_config.json` with strategy (repo/skill), search queries, and role_context
- "repo" strategy = specific project (e.g., "Open Claw experience")
- "skill" strategy = general skill area (e.g., "blockchain developer")

### Step 2: oxylabs-scraper.py
```bash
python oxylabs-scraper.py --limit N
```
- Reads `search_config.json` automatically if present
- `--limit N` restricts how many repos to enrich (useful for testing)
- Outputs `output.txt` with repos, contributors, and role_context
- Takes ~1-5 min depending on number of repos

### Step 3: data-enrichment.py
```bash
python data-enrichment.py
```
- Reads `output.txt`, outputs `enriched_output.txt`
- Enriches with LinkedIn data via EnrichLayer API
- Takes ~2-5 min due to rate limiting (2 req/60s on EnrichLayer)

### Step 4: profile_contributors.py
```bash
python profile_contributors.py
```
- Reads `enriched_output.txt`, outputs `contributor_profiles.json` + `contributor_profiles_report.md`
- Uses Claude to assess each contributor — takes ~5-7 min for 50+ contributors

### Step 5: rank_contributors.py
```bash
python rank_contributors.py
```
- Reads `contributor_profiles.json`, outputs `top_50_contributors.json` + `top_50_report.md`
- Re-assesses with salary context — takes ~5-7 min for 50+ contributors

## Testing Tips

### Use --limit for faster tests
With `--limit 2` the full pipeline takes ~10-15 min. With `--limit 5` it takes ~20-30 min.

### Validate dynamic role context flow
Check that each step uses the role from `search_config.json`:
- Step 2 console: "Loaded search config (X strategy)" and "Role: <title>"
- Step 4 console: "Role context: <title>"
- Step 4 report title: "<title> — Contributor Profiles Report"
- Step 5 console: "Role context: <title>"
- Step 5 JSON: `ranking_criteria.target_role` = dynamic title
- Step 5 report title: "<title> — Top Contributor Ranking Report"

### Common Issues
- **Contributor count parsing**: Numbers with commas (e.g., "1,622") in the GitHub contributor count span need `.replace(",", "")` before `int()` conversion. This was fixed but might recur if HTML format changes.
- **Oxylabs Google search 400 errors**: The Oxylabs API may return 400 errors for Google `site:linkedin.com` searches. This affects data-enrichment.py LinkedIn lookups. Not all contributors will be enriched — this is expected.
- **EnrichLayer rate limiting**: 2 requests per 60 seconds. The script handles this with automatic sleep, but it makes Step 3 slow.
- **Claude bots in contributor lists**: The scraper filters out known bot accounts (github-actions, dependabot, claude, copilot, etc.). If new bots appear, add them to the `NON_HUMAN` set in oxylabs-scraper.py.

### Test both strategies
For thorough testing, run with:
1. A repo-specific request: `python determine_search.py "Find developers with Open Claw experience"` (should return strategy=repo)
2. A skill-based request: `python determine_search.py "I need to hire a blockchain developer"` (should return strategy=skill)
