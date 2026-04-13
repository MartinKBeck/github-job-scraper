# GitHub Job Scraper

> Automated recruitment pipeline for identifying and ranking high-value open-source contributors.

## Overview

This pipeline scrapes GitHub to surface leads on engineers who may be open to new job opportunities. It uses Claude AI to intelligently determine search strategies, scrapes public GitHub data (profiles, repositories, activity), enriches candidates with LinkedIn data, and produces a ranked recruitment report.

## Pipeline Flow

```
 Your Hiring Request
        |
        v
 +---------------------+
 | 1. determine_search  |   "Find OpenClaw contributors" or "I need a blockchain developer"
 |    (Claude AI)       |   -> Determines: repo-specific search vs. skill-based search
 +---------------------+   -> Outputs: search_config.json (queries + role context)
        |
        v
 +---------------------+
 | 2. oxylabs-scraper   |   Scrapes GitHub search results for each query
 |    (OxyLabs API)     |   -> Fetches repo metadata, contributor lists, profiles
 +---------------------+   -> Outputs: output.txt (repos + contributors + role context)
        |
        v
 +---------------------+
 | 3. data-enrichment   |   Resolves LinkedIn profiles via multiple strategies:
 |    (Enrich Layer,    |     - Email reverse lookup
 |     Claude, OxyLabs) |     - Name + company lookup (Claude-extracted)
 +---------------------+     - Google site-search fallback
        |                  -> Outputs: enriched_output.txt
        v
 +---------------------+
 | 4. profile_contribs  |   Claude evaluates each contributor:
 |    (Claude AI)       |     - Profile summary
 +---------------------+     - Relevant skillset score (1-5)
        |                     - Hireability score (1-5)
        v                  -> Outputs: contributor_profiles.json + .md report
 +---------------------+
 | 5. rank_contributors |   Claude re-assesses with salary context ($250k-$400k):
 |    (Claude AI)       |     - Salary-adjusted hireability
 +---------------------+     - Location scoring (SF / US / International)
                              - Composite weighted ranking
                           -> Outputs: top_50_contributors.json + top_50_report.md
```

## Step Details

### Step 1: Determine Search Strategy (`determine_search.py`)

Uses Claude to analyze your natural language hiring request and decide the optimal GitHub search approach:

- **Repo mode**: If your request references a specific project (e.g. "OpenClaw", "TensorFlow"), it generates targeted search queries for that repository.
- **Skill mode**: If your request describes a general skill area (e.g. "blockchain developer", "ML engineer"), it recommends 3-5 GitHub repo search queries covering different angles (frameworks, tools, protocols, research areas).

Also produces a **role context** (title, key skills, description) that flows through the entire pipeline, so all downstream Claude evaluations are tailored to your specific hiring need.

```bash
python determine_search.py "Find developers with OpenClaw experience"
python determine_search.py "I need a blockchain developer"
python determine_search.py "Find React Native mobile engineers"
```

### Step 2: Scrape GitHub (`oxylabs-scraper.py`)

Scrapes GitHub repository search results via the OxyLabs Realtime API. For each repository found:
- Extracts metadata (stars, forks, watchers, language)
- Fetches the contributor list
- Scrapes each contributor's profile (bio, email, website, pinned repos)
- Fetches profile READMEs and commit counts via the GitHub API

Can run standalone with a direct query, or automatically reads `search_config.json` from Step 1:

```bash
# Using search config from Step 1 (recommended)
python oxylabs-scraper.py

# Direct query (bypasses Step 1)
python oxylabs-scraper.py "open claw"

# Limit repos for testing
python oxylabs-scraper.py --limit 2
```

### Step 3: Data Enrichment (`data-enrichment.py`)

Enriches contributor profiles with professional data from outside GitHub:
1. **Email reverse lookup** via Enrich Layer API
2. **Name + company lookup** using Claude-extracted professional info (job title, company, domain)
3. **Google site-search fallback** via OxyLabs for LinkedIn profile discovery

Includes a sliding-window rate limiter for the Enrich Layer free tier (2 req/min).

```bash
python data-enrichment.py
```

### Step 4: Profile Contributors (`profile_contributors.py`)

Uses Claude to generate a structured assessment for each contributor:
- **Profile summary**: 2-3 sentence overview of the person
- **Relevant skillset** (1-5): How relevant their skills are to the target role
- **Hireability** (1-5): How likely they are to be open to recruitment

The evaluation prompt is dynamically tailored using the role context from Step 1.

```bash
python profile_contributors.py
```

### Step 5: Rank Contributors (`rank_contributors.py`)

Final ranking step that uses Claude to re-assess each contributor with salary context ($250k-$400k):
- **Salary-adjusted hireability**: Accounts for academic vs. industry compensation
- **Location scoring**: San Francisco (5), Within US (3), Outside US (1)
- **Composite score**: 45% skillset + 35% hireability + 20% location

Produces a polished markdown report with an executive summary, ranking table, and detailed profiles for the top 20 candidates.

```bash
python rank_contributors.py
```

## Quick Start

```bash
# 1. Install dependencies
pip install -r requirements.txt

# 2. Configure environment variables
cp .env.example .env
# Edit .env and set:
#   ANTHROPIC_API_KEY=your_key_here
#   OXYLABS_USERNAME=your_username
#   OXYLABS_PASSWORD=your_password
#   GITHUB_TOKEN=your_token (optional, raises rate limit)
#   ENRICHLAYER_API_KEY=your_key (optional, for LinkedIn enrichment)

# 3. Run the full pipeline
python determine_search.py "your hiring request here"
python oxylabs-scraper.py
python data-enrichment.py
python profile_contributors.py
python rank_contributors.py

# 4. View results
cat top_50_report.md
```

## Configuration

### Environment Variables

| Variable | Required | Description |
|---|---|---|
| `ANTHROPIC_API_KEY` | Yes | Claude API key for AI-powered analysis |
| `OXYLABS_USERNAME` | Yes | OxyLabs Realtime API credentials |
| `OXYLABS_PASSWORD` | Yes | OxyLabs Realtime API credentials |
| `GITHUB_TOKEN` | No | GitHub PAT (raises API rate limit from 60 to 5,000 req/hr) |
| `ENRICHLAYER_API_KEY` | No | Enrich Layer API key for LinkedIn enrichment |

### Pipeline Artifacts

| File | Produced By | Description |
|---|---|---|
| `search_config.json` | `determine_search.py` | Search strategy, queries, and role context |
| `output.txt` | `oxylabs-scraper.py` | Raw scraped repo and contributor data |
| `enriched_output.txt` | `data-enrichment.py` | Contributors enriched with LinkedIn data |
| `contributor_profiles.json` | `profile_contributors.py` | Claude-generated profile assessments |
| `contributor_profiles_report.md` | `profile_contributors.py` | Human-readable profiles report |
| `top_50_contributors.json` | `rank_contributors.py` | Final ranked candidate data |
| `top_50_report.md` | `rank_contributors.py` | Final recruitment report |

## Notes

- Respects GitHub API rate limits (authenticated: 5,000 req/hr)
- Only accesses publicly available data
- All Claude prompts are dynamically tailored to your specific hiring request
- The pipeline is modular: you can re-run any step independently
- Enrich Layer rate limiter defaults to 2 req/min (free tier); adjust in `data-enrichment.py` for paid tiers
