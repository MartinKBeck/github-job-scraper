# GitHub Job Scraper

> One-day sprint project — built in a single coding session.

## Overview

This application scrapes GitHub to surface leads on engineers who may be open to new job opportunities. It queries public GitHub data (profiles, repositories, activity) and compiles a list of potential candidates based on configurable criteria.

## Goals

- Identify active engineers on GitHub based on signals like recent activity, tech stack, and location
- Export structured lead data for outreach (name, GitHub profile, skills, contact info where available)
- Keep it simple: this is a sprint, not a production system

## How It Works

1. Query the GitHub API for users matching target criteria (language, location, activity, etc.)
2. Enrich each profile with relevant data points (repos, stars, bio, public email)
3. Output results as a structured list (CSV or JSON)

## Setup

```bash
# Install dependencies
npm install

# Add your GitHub personal access token
cp .env.example .env
# Edit .env and set GITHUB_TOKEN=your_token_here

# Run the scraper
npm start
```

## Configuration

Edit the search parameters in `config.js` (or equivalent) to target specific:

- Programming languages
- Location / region
- Minimum repo count or star count
- Account activity recency

## Output

Results are written to `output/leads.csv` (or `.json`) and include:

| Field | Description |
|---|---|
| `username` | GitHub handle |
| `name` | Display name |
| `email` | Public email (if set) |
| `location` | Self-reported location |
| `languages` | Top languages used |
| `repos` | Public repo count |
| `stars` | Total stars received |
| `profile_url` | Link to GitHub profile |

## Notes

- Respects GitHub API rate limits (authenticated: 5,000 req/hr)
- Only accesses publicly available data
- Built as a one-day sprint — expect rough edges
