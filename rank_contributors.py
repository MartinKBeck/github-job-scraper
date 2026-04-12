# rank_contributors.py
#
# Reads contributor_profiles.json (output from profile_contributors.py),
# re-evaluates hireability using Claude with specific $250k-$400k salary context,
# computes a composite ranking score, and outputs:
#   - top_50_contributors.json  (structured ranked data)
#   - top_50_report.md          (polished markdown report)

import json
import os
import re
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
INPUT_PATH = "contributor_profiles.json"
ENRICHED_PATH = "enriched_output.txt"
JSON_OUTPUT_PATH = "top_50_contributors.json"
MD_OUTPUT_PATH = "top_50_report.md"

SKILLSET_WEIGHT = 0.5
HIREABILITY_WEIGHT = 0.3
LOCATION_WEIGHT = 0.2
TOP_N = 50

LOCATION_SCORES = {
    "san_francisco": 5,
    "within_us": 3,
    "outside_us": 1,
}


def load_enriched_data(path: str) -> dict:
    """Load enriched_output.txt and build a lookup by username."""
    lookup = {}
    if not os.path.exists(path):
        return lookup
    with open(path) as f:
        data = json.load(f)
    for repo in data.get("repositories", []):
        for contributor in repo.get("contributors", []):
            username = contributor.get("username")
            if username:
                lookup[username] = contributor
    return lookup


def build_reassessment_context(profile: dict, enriched: dict | None) -> str:
    """Build context string for Claude re-assessment with salary context."""
    parts = []
    parts.append(f"Name: {profile['name']}")
    parts.append(f"GitHub Username: {profile['username']}")
    parts.append(f"Repository: {profile['repository']}")
    parts.append(f"Profile Summary: {profile.get('profile_summary', 'N/A')}")

    skill = profile.get("relevant_skillset", {})
    parts.append(f"Relevant Skillset Score: {skill.get('score', '?')}/5")
    parts.append(f"Skillset Justification: {skill.get('justification', 'N/A')}")

    hire = profile.get("hireability", {})
    parts.append(f"Original Hireability Score: {hire.get('score', '?')}/5")
    parts.append(f"Original Hireability Justification: {hire.get('justification', 'N/A')}")

    if enriched:
        if enriched.get("bio"):
            parts.append(f"GitHub Bio: {enriched['bio']}")
        if enriched.get("email"):
            parts.append(f"Public Email: {enriched['email']}")
        if enriched.get("website"):
            parts.append(f"Website: {enriched['website']}")
        if enriched.get("location"):
            parts.append(f"Location: {enriched['location']}")
        if enriched.get("linkedin_profile_url"):
            parts.append(f"LinkedIn: {enriched['linkedin_profile_url']}")
        li_data = enriched.get("linkedin_profile_data")
        if li_data:
            if li_data.get("current_role"):
                parts.append(f"Current Role: {li_data['current_role']}")
            if li_data.get("current_company"):
                parts.append(f"Current Company: {li_data['current_company']}")
            if li_data.get("time_at_role"):
                parts.append(f"Time at Role: {li_data['time_at_role']}")

    return "\n".join(parts)


def reassess_contributor(client: anthropic.Anthropic, profile: dict, enriched: dict | None) -> dict:
    """Use Claude to re-assess hireability with $250k-$400k salary context."""
    context = build_reassessment_context(profile, enriched)
    context = context.encode("utf-8", errors="replace").decode("utf-8")

    prompt = (
        "You are a technical recruiter evaluating candidates for a startup building OpenClaw, "
        "an open-source RL framework for training Computer Use agents. The position offers "
        "$250,000 - $400,000 total compensation.\n\n"
        "Given the following candidate data, re-evaluate their hireability specifically "
        "considering this salary range and recruitment context:\n\n"
        "KEY FACTORS:\n"
        "- Academic researchers (postdocs, PhD students) are often very hireable because "
        "  $250k-$400k far exceeds academic compensation\n"
        "- People already at top-tier companies in senior roles may be less hireable unless "
        "  they show signs of being open to new opportunities\n"
        "- Early-career researchers finishing PhDs are prime candidates\n"
        "- People with public contact info (email, active profiles) are more accessible\n"
        "- Contributors with deep domain expertise (RL, agents, LLMs) command this salary range\n"
        "- Consider whether the person would see this as a significant step up financially\n\n"
        "Return ONLY valid JSON with these fields:\n"
        "1. \"hireability\": object with:\n"
        "   - \"score\" (integer 1-5): Re-assessed hireability considering the $250k-$400k offer\n"
        "   - \"justification\": Brief explanation considering salary context\n"
        "2. \"location\": object with:\n"
        "   - \"category\": One of \"san_francisco\", \"within_us\", or \"outside_us\"\n"
        "     Determine the candidate's likely location from all available signals (bio, "
        "company HQ, LinkedIn, email domain, university, timezone clues, etc.).\n"
        "     - \"san_francisco\" = located in San Francisco or the immediate SF Bay Area\n"
        "     - \"within_us\" = located in the United States but not in the SF Bay Area\n"
        "     - \"outside_us\" = located outside the United States, or location completely unknown\n"
        "   - \"justification\": Brief explanation of how you determined the location\n"
        "3. \"recruitment_recommendation\": 1-2 sentences on the best approach to recruit this person\n\n"
        "Scoring guide for hireability:\n"
        "  1 = Very unlikely to recruit, 2 = Low chance, 3 = Moderate chance, "
        "4 = High chance, 5 = Very likely to recruit\n\n"
        "Return ONLY valid JSON. No markdown fences, no extra text.\n\n"
        f"--- CANDIDATE DATA ---\n{context}"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(f"    WARNING: Claude returned non-JSON for {profile.get('username')}: {raw[:200]}")
        result = {
            "hireability": {"score": profile.get("hireability", {}).get("score", 0),
                            "justification": "Re-assessment failed; using original score."},
            "location": {"category": "outside_us",
                         "justification": "Re-assessment failed; defaulting to outside_us."},
            "recruitment_recommendation": "Unable to generate recommendation.",
        }

    # Normalise location if Claude returned an unexpected category
    loc = result.get("location") or {}
    if loc.get("category") not in LOCATION_SCORES:
        loc["category"] = "outside_us"
        result["location"] = loc

    return result


def compute_composite_score(skillset_score: int, hireability_score: int, location_score: int) -> float:
    """Compute weighted composite score."""
    return round(
        SKILLSET_WEIGHT * skillset_score
        + HIREABILITY_WEIGHT * hireability_score
        + LOCATION_WEIGHT * location_score,
        2,
    )


def generate_ranked_json(ranked_candidates: list, total: int) -> dict:
    """Build the final JSON output structure."""
    return {
        "ranking_criteria": {
            "salary_range": "$250k - $400k",
            "target_skillset": "OpenClaw / RL / Computer Use Agents",
            "weights": {
                "relevant_skillset": SKILLSET_WEIGHT,
                "hireability": HIREABILITY_WEIGHT,
                "location": LOCATION_WEIGHT,
            },
            "location_scoring": {
                "san_francisco": LOCATION_SCORES["san_francisco"],
                "within_us": LOCATION_SCORES["within_us"],
                "outside_us": LOCATION_SCORES["outside_us"],
            },
        },
        "total_candidates": total,
        "top_candidates": ranked_candidates,
    }


def generate_markdown_report(ranked_candidates: list, total: int) -> str:
    """Generate a polished markdown report."""
    lines = []

    # Header
    lines.append("# Top Contributor Ranking Report")
    lines.append("")
    lines.append(f"*Generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")

    # Executive Summary
    lines.append("## Executive Summary")
    lines.append("")
    lines.append(
        f"This report ranks **{total}** contributors from OpenClaw-related repositories "
        f"based on their technical skillset relevance, hireability, and location for positions "
        f"offering **$250,000 - $400,000** total compensation. Contributors were evaluated using "
        f"a composite scoring algorithm that weights relevant skillset (50%), hireability (30%), "
        f"and location (20%)."
    )
    lines.append("")
    if ranked_candidates:
        top = ranked_candidates[0]
        lines.append(
            f"The top-ranked candidate is **{top['name']}** (@{top['username']}) with a "
            f"composite score of **{top['composite_score']}**."
        )
    lines.append("")

    # Methodology
    lines.append("## Ranking Methodology")
    lines.append("")
    lines.append("Each contributor is scored on two dimensions:")
    lines.append("")
    lines.append("| Dimension | Weight | Description |")
    lines.append("|-----------|--------|-------------|")
    lines.append(
        "| Relevant Skillset | 50% | RL experience, LLM/agent work, Python proficiency, "
        "contributions to OpenClaw or similar projects |"
    )
    lines.append(
        "| Hireability | 30% | Likelihood of accepting a $250k-$400k offer, considering "
        "current role, career stage, accessibility, and financial incentive |"
    )
    lines.append(
        "| Location | 20% | Candidate proximity: San Francisco (5), "
        "Within US (3), Outside US (1) |"
    )
    lines.append("")
    lines.append("**Composite Score** = 0.5 x Skillset + 0.3 x Hireability + 0.2 x Location (max 5.0)")
    lines.append("")
    lines.append(
        "Hireability was re-assessed by Claude with specific context about the $250k-$400k "
        "salary range, which significantly exceeds academic compensation and is competitive "
        "with top industry roles."
    )
    lines.append("")

    # Summary Table
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Rank | Name | Username | Skillset | Hireability | Location | Composite | Recommendation |")
    lines.append("|-----:|------|----------|:--------:|:-----------:|:--------:|:---------:|----------------|")

    for c in ranked_candidates:
        rec = c.get("recruitment_recommendation", "N/A")
        # Truncate long recommendations for table
        if len(rec) > 80:
            rec_short = rec[:77] + "..."
        else:
            rec_short = rec
        loc = c.get("location", {})
        loc_cat = loc.get("category", "outside_us")
        loc_label = {"san_francisco": "SF", "within_us": "US", "outside_us": "Intl"}.get(loc_cat, "?")
        loc_score = LOCATION_SCORES.get(loc_cat, 1)
        lines.append(
            f"| {c['rank']} | {c['name']} | @{c['username']} "
            f"| {c.get('relevant_skillset', {}).get('score', '?')}/5 "
            f"| {c.get('hireability', {}).get('score', '?')}/5 "
            f"| {loc_label} ({loc_score}/5) "
            f"| **{c['composite_score']}** "
            f"| {rec_short} |"
        )

    lines.append("")

    # Detailed profiles for top 10
    top_10 = ranked_candidates[:10]
    lines.append("---")
    lines.append("")
    lines.append("## Detailed Profiles (Top 10)")
    lines.append("")

    for c in top_10:
        lines.append(f"### #{c['rank']}. {c['name']} (@{c['username']})")
        lines.append("")
        lines.append(f"**Repository:** {c['repository']}")
        lines.append("")
        lines.append(f"**Composite Score:** {c['composite_score']}/5.0")
        lines.append("")
        lines.append(f"**Profile Summary:** {c.get('profile_summary', 'N/A')}")
        lines.append("")

        skill = c.get("relevant_skillset", {})
        lines.append(f"**Relevant Skillset:** {skill.get('score', '?')}/5")
        lines.append(f"> {skill.get('justification', 'N/A')}")
        lines.append("")

        hire = c.get("hireability", {})
        lines.append(f"**Hireability (Salary-Adjusted):** {hire.get('score', '?')}/5")
        lines.append(f"> {hire.get('justification', 'N/A')}")
        lines.append("")

        loc = c.get("location", {})
        loc_cat = loc.get("category", "outside_us")
        loc_label = {"san_francisco": "San Francisco", "within_us": "Within US", "outside_us": "Outside US"}.get(loc_cat, "Unknown")
        loc_score = LOCATION_SCORES.get(loc_cat, 1)
        lines.append(f"**Location:** {loc_label} ({loc_score}/5)")
        lines.append(f"> {loc.get('justification', 'N/A')}")
        lines.append("")

        lines.append(f"**Recruitment Recommendation:** {c.get('recruitment_recommendation', 'N/A')}")
        lines.append("")
        lines.append("---")
        lines.append("")

    # Quick reference for remaining candidates
    remaining = ranked_candidates[10:]
    if remaining:
        lines.append("## Quick Reference (Remaining Candidates)")
        lines.append("")
        for c in remaining:
            loc = c.get("location", {})
            loc_cat = loc.get("category", "outside_us")
            loc_label = {"san_francisco": "SF", "within_us": "US", "outside_us": "Intl"}.get(loc_cat, "?")
            loc_score = LOCATION_SCORES.get(loc_cat, 1)
            lines.append(
                f"- **#{c['rank']} {c['name']}** (@{c['username']}) - "
                f"Composite: {c['composite_score']} | "
                f"Skillset: {c.get('relevant_skillset', {}).get('score', '?')}/5 | "
                f"Hireability: {c.get('hireability', {}).get('score', '?')}/5 | "
                f"Location: {loc_label} ({loc_score}/5) | "
                f"{c.get('recruitment_recommendation', 'N/A')}"
            )
        lines.append("")

    return "\n".join(lines)


def main():
    if not ANTHROPIC_API_KEY:
        raise EnvironmentError("ANTHROPIC_API_KEY must be set in your .env file.")

    # Load profiles
    print(f"Loading profiles from {INPUT_PATH}...")
    with open(INPUT_PATH) as f:
        data = json.load(f)
    profiles = data.get("profiles", [])
    print(f"  Found {len(profiles)} contributor profiles.")

    # Load enriched data for additional context
    enriched_lookup = load_enriched_data(ENRICHED_PATH)
    print(f"  Loaded enriched data for {len(enriched_lookup)} contributors.")

    # Re-assess each contributor with salary context
    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    ranked = []

    print("\nRe-assessing hireability with $250k-$400k salary context...")
    for profile in profiles:
        username = profile.get("username", "?")
        print(f"  Re-assessing {username}...")

        enriched = enriched_lookup.get(username)
        reassessment = reassess_contributor(client, profile, enriched)

        # Use original skillset score, updated hireability, and new location
        skillset_score = (profile.get("relevant_skillset") or {}).get("score") or 0
        new_hireability = reassessment.get("hireability") or {}
        hireability_score = new_hireability.get("score") or 0
        new_location = reassessment.get("location") or {"category": "outside_us", "justification": "No location data."}
        location_category = new_location.get("category", "outside_us")
        location_score = LOCATION_SCORES.get(location_category, 1)
        composite = compute_composite_score(skillset_score, hireability_score, location_score)

        candidate = {
            "rank": 0,  # Assigned after sorting
            "name": profile["name"],
            "username": profile["username"],
            "repository": profile["repository"],
            "profile_summary": profile.get("profile_summary", ""),
            "relevant_skillset": profile.get("relevant_skillset", {}),
            "hireability": new_hireability,
            "location": new_location,
            "composite_score": composite,
            "recruitment_recommendation": reassessment.get("recruitment_recommendation", ""),
        }
        ranked.append(candidate)

        loc_label = {"san_francisco": "SF", "within_us": "US", "outside_us": "Intl"}.get(location_category, "?")
        print(
            f"    Skillset: {skillset_score}/5, "
            f"Hireability: {hireability_score}/5 "
            f"(was {profile.get('hireability', {}).get('score', '?')}), "
            f"Location: {loc_label} ({location_score}/5), "
            f"Composite: {composite}"
        )

    # Sort by composite score descending, then by skillset as tiebreaker
    ranked.sort(
        key=lambda c: (c["composite_score"], c["relevant_skillset"].get("score", 0)),
        reverse=True,
    )

    # Assign ranks and take top N
    for i, candidate in enumerate(ranked, 1):
        candidate["rank"] = i
    top_candidates = ranked[:TOP_N]
    total = len(ranked)

    # Write JSON output
    output = generate_ranked_json(top_candidates, total)
    with open(JSON_OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nRanked JSON written to {JSON_OUTPUT_PATH}")

    # Write markdown report
    md_report = generate_markdown_report(top_candidates, total)
    with open(MD_OUTPUT_PATH, "w") as f:
        f.write(md_report)
    print(f"Markdown report written to {MD_OUTPUT_PATH}")

    print(f"\nDone. Ranked {total} contributors, top {len(top_candidates)} saved.")


if __name__ == "__main__":
    main()
