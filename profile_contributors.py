# profile_contributors.py
#
# Reads enriched_output.txt (produced by data-enrichment.py), calls the
# Anthropic Claude API to generate a profile assessment for each contributor,
# and outputs:
#   - contributor_profiles.json   (structured data)
#   - contributor_profiles_report.md  (human-readable report)

import json
import os
import re
from datetime import datetime, timezone

import anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
INPUT_PATH = "enriched_output.txt"
SEARCH_CONFIG_PATH = "search_config.json"
JSON_OUTPUT_PATH = "contributor_profiles.json"
MD_OUTPUT_PATH = "contributor_profiles_report.md"

# Fallback role context when no search_config.json is present (original behavior)
DEFAULT_ROLE_CONTEXT = {
    "title": "RL / Computer Use Agent Engineer",
    "key_skills": ["reinforcement learning", "LLM agents", "Python", "computer use"],
    "description": (
        "Engineer with experience in RL frameworks, LLM/agent work, and Python, "
        "ideally with contributions to OpenClaw or similar open-source projects."
    ),
}


def load_role_context() -> dict:
    """Load role context from search_config.json or enriched_output.txt, with fallback."""
    # Try search_config.json first
    if os.path.exists(SEARCH_CONFIG_PATH):
        with open(SEARCH_CONFIG_PATH) as f:
            config = json.load(f)
        if config.get("role_context"):
            return config["role_context"]

    # Try role_context embedded in enriched_output.txt
    if os.path.exists(INPUT_PATH):
        with open(INPUT_PATH) as f:
            data = json.load(f)
        if data.get("role_context"):
            return data["role_context"]

    return DEFAULT_ROLE_CONTEXT


def build_contributor_context(contributor: dict, repo_name: str) -> str:
    """Build a text summary of all available data for a contributor."""
    parts = []

    parts.append(f"Repository: {repo_name}")

    if contributor.get("name"):
        parts.append(f"Name: {contributor['name']}")
    if contributor.get("username"):
        parts.append(f"GitHub Username: {contributor['username']}")
    if contributor.get("bio"):
        parts.append(f"GitHub Bio: {contributor['bio']}")
    if contributor.get("extended_bio"):
        bio_text = contributor["extended_bio"][:2000]
        parts.append(f"Extended Bio / Profile README:\n{bio_text}")
    if contributor.get("email"):
        parts.append(f"Email: {contributor['email']}")
    if contributor.get("website"):
        parts.append(f"Website: {contributor['website']}")
    if contributor.get("commits_to_repo"):
        parts.append(f"Commits to {repo_name}: {contributor['commits_to_repo']}")

    pinned = contributor.get("pinned_repositories") or []
    if pinned:
        pinned_lines = []
        for p in pinned:
            desc = p.get("description") or ""
            stars = p.get("stars") or ""
            lang = p.get("language") or ""
            pinned_lines.append(
                f"  - {p.get('name', '?')}: {desc} "
                f"[stars: {stars}, lang: {lang}]"
            )
        parts.append("Pinned Repositories:\n" + "\n".join(pinned_lines))

    if contributor.get("linkedin_profile_url"):
        parts.append(f"LinkedIn URL: {contributor['linkedin_profile_url']}")

    li_data = contributor.get("linkedin_profile_data")
    if li_data:
        li_parts = []
        if li_data.get("current_role"):
            li_parts.append(f"Current Role: {li_data['current_role']}")
        if li_data.get("current_company"):
            li_parts.append(f"Current Company: {li_data['current_company']}")
        if li_data.get("time_at_role"):
            li_parts.append(f"Time at Role: {li_data['time_at_role']}")
        if li_parts:
            parts.append("LinkedIn Profile Data:\n  " + "\n  ".join(li_parts))

    return "\n".join(parts)


def profile_contributor(
    client: anthropic.Anthropic,
    contributor: dict,
    repo_name: str,
    role_context: dict | None = None,
) -> dict:
    """Call Claude to generate a profile assessment for one contributor."""
    context = build_contributor_context(contributor, repo_name)
    # Sanitize surrogate characters
    context = context.encode("utf-8", errors="replace").decode("utf-8")

    rc = role_context or DEFAULT_ROLE_CONTEXT
    role_title = rc.get("title", "Software Engineer")
    role_desc = rc.get("description", "")
    key_skills = rc.get("key_skills", [])
    skills_str = ", ".join(key_skills) if key_skills else "relevant technical skills"

    prompt = (
        f"You are evaluating contributors to open-source repositories for a "
        f"{role_title} role.\n"
        f"Role description: {role_desc}\n"
        f"Key skills to evaluate: {skills_str}\n\n"
        "Given the following contributor data, produce a JSON object with these fields:\n"
        "1. \"profile_summary\": A 2-3 sentence profile summary of this person.\n"
        "2. \"relevant_skillset\": An object with:\n"
        f"   - \"score\" (integer 1-5): How relevant is this person's skillset to the "
        f"{role_title} role? Score based on: experience with {skills_str}, contributions "
        "to relevant open-source projects, and research in related areas\n"
        "   - \"justification\": Brief explanation for the score\n"
        "3. \"hireability\": An object with:\n"
        "   - \"score\" (integer 1-5): How likely is this person to be open to recruitment? "
        "Consider: current role stability (academic vs industry, tenure), whether they're "
        "actively looking (bio signals, 'open to work'), seniority level, and accessibility "
        "(public email, active profile)\n"
        "   - \"justification\": Brief explanation for the score\n\n"
        "Scoring guide:\n"
        "  1 = Very low, 2 = Low, 3 = Moderate, 4 = High, 5 = Very high\n\n"
        "Return ONLY valid JSON. No markdown fences, no extra text.\n\n"
        f"--- CONTRIBUTOR DATA ---\n{context}"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=600,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(f"    WARNING: Claude returned non-JSON for {contributor.get('username')}: {raw[:200]}")
        result = {
            "profile_summary": "Unable to generate profile — Claude response was not valid JSON.",
            "relevant_skillset": {"score": 0, "justification": "N/A"},
            "hireability": {"score": 0, "justification": "N/A"},
        }

    return {
        "name": contributor.get("name") or contributor.get("username") or "Unknown",
        "username": contributor.get("username") or "Unknown",
        "repository": repo_name,
        "profile_summary": result.get("profile_summary", ""),
        "relevant_skillset": result.get("relevant_skillset", {}),
        "hireability": result.get("hireability", {}),
    }


def generate_markdown_report(profiles: list, role_context: dict | None = None) -> str:
    """Generate a human-readable markdown report from the profile list."""
    rc = role_context or DEFAULT_ROLE_CONTEXT
    role_title = rc.get("title", "Software Engineer")
    lines = []
    lines.append(f"# {role_title} — Contributor Profiles Report")
    lines.append("")
    lines.append(f"*Generated on {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}*")
    lines.append("")
    lines.append("## Summary Table")
    lines.append("")
    lines.append("| Name | Username | Repository | Skillset (1-5) | Hireability (1-5) |")
    lines.append("|------|----------|------------|:--------------:|:-----------------:|")

    for p in profiles:
        skill_score = p.get("relevant_skillset", {}).get("score", "?")
        hire_score = p.get("hireability", {}).get("score", "?")
        lines.append(
            f"| {p['name']} | @{p['username']} | {p['repository']} "
            f"| {skill_score} | {hire_score} |"
        )

    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Detailed Profiles")
    lines.append("")

    for i, p in enumerate(profiles, 1):
        lines.append(f"### {i}. {p['name']} (@{p['username']})")
        lines.append("")
        lines.append(f"**Repository:** {p['repository']}")
        lines.append("")
        lines.append(f"**Profile Summary:** {p.get('profile_summary', 'N/A')}")
        lines.append("")

        skill = p.get("relevant_skillset", {})
        lines.append(f"**Relevant Skillset:** {skill.get('score', '?')}/5")
        lines.append(f"> {skill.get('justification', 'N/A')}")
        lines.append("")

        hire = p.get("hireability", {})
        lines.append(f"**Hireability:** {hire.get('score', '?')}/5")
        lines.append(f"> {hire.get('justification', 'N/A')}")
        lines.append("")
        lines.append("---")
        lines.append("")

    return "\n".join(lines)


def main():
    if not ANTHROPIC_API_KEY:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY must be set in your .env file."
        )

    with open(INPUT_PATH) as f:
        data = json.load(f)

    # Load role context for dynamic prompts
    role_context = data.get("role_context") or load_role_context()
    role_title = role_context.get("title", "Software Engineer")
    print(f"  Role context: {role_title}")
    if role_context.get("key_skills"):
        print(f"  Key skills: {', '.join(role_context['key_skills'])}")
    print()

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
    profiles = []

    for repo in data.get("repositories", []):
        repo_name = repo.get("name", "Unknown")
        contributors = repo.get("contributors", [])
        if not contributors:
            continue

        print(f"Processing {repo_name} ({len(contributors)} contributors)...")

        for contributor in contributors:
            username = contributor.get("username", "?")
            print(f"  Profiling {username}...")
            profile = profile_contributor(client, contributor, repo_name, role_context)
            profiles.append(profile)
            skill = profile.get("relevant_skillset", {}).get("score", "?")
            hire = profile.get("hireability", {}).get("score", "?")
            print(f"    Skillset: {skill}/5, Hireability: {hire}/5")

    # Write JSON output
    output = {"profiles": profiles}
    with open(JSON_OUTPUT_PATH, "w") as f:
        json.dump(output, f, indent=2)
    print(f"\nJSON profiles written to {JSON_OUTPUT_PATH}")

    # Write markdown report
    md_report = generate_markdown_report(profiles, role_context)
    with open(MD_OUTPUT_PATH, "w") as f:
        f.write(md_report)
    print(f"Markdown report written to {MD_OUTPUT_PATH}")

    print(f"\nDone. Profiled {len(profiles)} contributors.")


if __name__ == "__main__":
    main()
