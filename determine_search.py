# determine_search.py
#
# Pre-step for the GitHub job scraper pipeline.
# Uses Claude to analyze a natural language hiring request and determine
# the optimal GitHub search strategy:
#
#   - "Repo" mode:    The request maps to a specific GitHub repository
#                     (e.g. "find me OpenClaw contributors").
#                     Output: a targeted repo search query for oxylabs-scraper.py.
#
#   - "Skill" mode:   The request describes a general skill area
#                     (e.g. "find blockchain developers").
#                     Output: a list of recommended GitHub repo search queries
#                     to surface developers with that experience.
#
# Intended flow:
#   1. Run determine_search.py "your hiring request"  ->  search_config.json
#   2. Run oxylabs-scraper.py  (reads search_config.json)  ->  output.txt
#   3. Run data-enrichment.py   ->  enriched_output.txt
#   4. Run profile_contributors.py  ->  contributor_profiles.json
#   5. Run rank_contributors.py     ->  top_50_report.md

import argparse
import json
import os
import re
import sys

import anthropic
from dotenv import load_dotenv

load_dotenv()

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY")
OUTPUT_PATH = "search_config.json"


def determine_search_strategy(client: anthropic.Anthropic, request: str) -> dict:
    """
    Use Claude to analyze a hiring request and decide on the GitHub search
    strategy.  Returns a structured dict describing the search plan.
    """
    prompt = (
        "You are an expert technical recruiter assistant. A user has described "
        "the kind of developer they want to hire. Your job is to determine the "
        "best GitHub search strategy to find candidates.\n\n"
        "There are two possible strategies:\n\n"
        "1. **repo** - The request references a specific open-source project or "
        "GitHub repository. In this case, the best approach is to scrape that "
        "repository's contributors directly.\n"
        "   Examples:\n"
        "   - 'Find developers with OpenClaw experience' -> search for the OpenClaw repo\n"
        "   - 'I need someone who works on React' -> search for facebook/react repo\n"
        "   - 'Get me contributors to TensorFlow' -> search for tensorflow/tensorflow repo\n\n"
        "2. **skill** - The request describes a general skill, technology area, or "
        "developer profile rather than a specific project. In this case, recommend "
        "the top GitHub repository searches that would surface developers with that "
        "experience.\n"
        "   Examples:\n"
        "   - 'Find a blockchain developer' -> suggest searches like 'ethereum solidity', "
        "'defi protocol', 'smart contract framework', etc.\n"
        "   - 'I need a machine learning engineer' -> suggest searches like "
        "'deep learning framework', 'pytorch extension', 'ml training pipeline', etc.\n\n"
        "Return ONLY valid JSON with these fields:\n"
        "{\n"
        '  "strategy": "repo" or "skill",\n'
        '  "reasoning": "Brief explanation of why you chose this strategy",\n'
        '  "search_queries": [\n'
        "    {\n"
        '      "query": "the GitHub search query string",\n'
        '      "description": "what this search targets and why"\n'
        "    }\n"
        "  ],\n"
        '  "role_context": {\n'
        '    "title": "suggested job title for the role (e.g. RL Engineer, Blockchain Developer)",\n'
        '    "key_skills": ["list", "of", "key", "technical", "skills"],\n'
        '    "description": "1-2 sentence description of the ideal candidate profile"\n'
        "  }\n"
        "}\n\n"
        "Guidelines:\n"
        "- For 'repo' strategy: provide 1-3 search queries that target the specific "
        "project. The first should be the most direct match. Include variant names "
        "or related repos if applicable.\n"
        "- For 'skill' strategy: provide 3-5 search queries, ordered by expected "
        "relevance. Each query should target a different angle to maximize coverage "
        "(e.g. frameworks, tools, protocols, research areas).\n"
        "- Always sort by stars descending (the scraper handles this).\n"
        "- The role_context will be used by downstream pipeline steps to evaluate "
        "candidates, so make it specific and accurate.\n\n"
        "Return ONLY valid JSON. No markdown fences, no extra text.\n\n"
        f"--- HIRING REQUEST ---\n{request}"
    )

    message = client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=800,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # Strip markdown code fences if present
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        print(f"WARNING: Claude returned non-JSON: {raw[:300]}")
        print("Falling back to generic search.")
        result = {
            "strategy": "skill",
            "reasoning": "Claude response was not valid JSON; using request as-is.",
            "search_queries": [
                {
                    "query": request,
                    "description": "Direct search using the original request text.",
                }
            ],
            "role_context": {
                "title": "Software Engineer",
                "key_skills": [],
                "description": request,
            },
        }

    # Validate required fields
    if "strategy" not in result:
        result["strategy"] = "skill"
    if "search_queries" not in result or not result["search_queries"]:
        result["search_queries"] = [
            {"query": request, "description": "Fallback: original request text."}
        ]
    if "role_context" not in result:
        result["role_context"] = {
            "title": "Software Engineer",
            "key_skills": [],
            "description": request,
        }

    return result


def main():
    parser = argparse.ArgumentParser(
        description=(
            "Pre-step: Use Claude to determine the optimal GitHub search strategy "
            "for a hiring request."
        ),
    )
    parser.add_argument(
        "request",
        nargs="?",
        default=None,
        help=(
            'Natural language hiring request, e.g. "Find developers with OpenClaw '
            'experience" or "I need a blockchain developer"'
        ),
    )
    parser.add_argument(
        "--output",
        default=OUTPUT_PATH,
        help=f"Output path for search config JSON (default: {OUTPUT_PATH})",
    )
    args = parser.parse_args()

    if not args.request:
        print("Usage: python determine_search.py \"<your hiring request>\"")
        print()
        print("Examples:")
        print('  python determine_search.py "Find developers with OpenClaw experience"')
        print('  python determine_search.py "I need a blockchain developer"')
        print('  python determine_search.py "Find React Native mobile engineers"')
        sys.exit(1)

    if not ANTHROPIC_API_KEY:
        raise EnvironmentError(
            "ANTHROPIC_API_KEY must be set in your .env file."
        )

    client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)

    print(f"Analyzing hiring request: \"{args.request}\"")
    print()

    config = determine_search_strategy(client, args.request)

    # Display results
    strategy = config["strategy"]
    print(f"Strategy: {strategy.upper()}")
    print(f"Reasoning: {config.get('reasoning', 'N/A')}")
    print()

    role = config.get("role_context", {})
    print(f"Role: {role.get('title', 'N/A')}")
    print(f"Profile: {role.get('description', 'N/A')}")
    if role.get("key_skills"):
        print(f"Key Skills: {', '.join(role['key_skills'])}")
    print()

    print("Recommended GitHub searches:")
    for i, sq in enumerate(config["search_queries"], 1):
        print(f"  {i}. \"{sq['query']}\"")
        print(f"     {sq.get('description', '')}")
    print()

    # Write config
    with open(args.output, "w") as f:
        json.dump(config, f, indent=2)
    print(f"Search config written to {args.output}")

    # Print next-step instructions
    print()
    print("Next steps:")
    if strategy == "repo":
        first_query = config["search_queries"][0]["query"]
        print(f'  python oxylabs-scraper.py "{first_query}"')
    else:
        print("  Run oxylabs-scraper.py for each recommended search query:")
        for sq in config["search_queries"]:
            print(f'    python oxylabs-scraper.py "{sq["query"]}"')
    print("  Then continue with: data-enrichment.py -> profile_contributors.py -> rank_contributors.py")


if __name__ == "__main__":
    main()
