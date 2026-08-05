#!/usr/bin/env python3
"""
Generate a repos.json file with information about repositories both from repos of trusted github
owners with `trame` topic and from the `external_repos.yml` file.

Fetches: name, url, description, image, topics, stars, commit count, PR count, creation date, most
recent commit date, and whether it was created within the last year.

```mermaid
flowchart LR
    subgraph Wrapper [
        info contains: name, description, topics, image url, stars, last commit date, creation date,
        commits count, PRs count.
    ]
        direction LR
        A[external_repos.yml]
        B[GH repos in<br> trusted_owners<br> list with <br>`trame` topic]
        C((+))
        A -- "trusted <br>+= False" --> C
        B -- "trusted <br>= True" --> C
        C -- "url: {<br> trusted,<br> info<br>}" --> D((x))
        E[Fetch GH <br>for info]
        D -- GH url --> E
        D -- non GH url --> F((+))
        E --> F
        F -- "+is_new<br>if created<br>within last<br>year" --> G[repos.json]
    end

    style Wrapper fill:none,stroke:none
```
"""

import subprocess
import os
import json
import re
from datetime import datetime, timedelta, timezone
import warnings
import yaml

EXTERNAL_REPOS_FILE = "external_repos.yml"
OUTPUT_FILE = "repos.json"


def minify_graphql(query):
    query = re.sub(r"#.*$", "", query, flags=re.MULTILINE)
    query = re.sub(r"\s+", " ", query)
    for char in ["{", "}", "(", ")", ":", ","]:
        query = query.replace(f" {char}", char)
        query = query.replace(f"{char} ", char)
    query = query.strip()
    return query


def make_gh_request(request: list[str]):
    result = subprocess.run(request, capture_output=True, text=True, env=os.environ)
    if result.returncode != 0:
        raise Exception(
            f"Error executing command {' '.join(request)}:\n{result.stderr}"
        )
    return result.stdout


def retrieve_gh_repos_from_topic(topic: str, owners: list = []):
    repos = {}
    for owner in owners:
        print(f"    Querying topic '{topic}' for owner '{owner}'...")
        request = [
            "gh",
            "search",
            "repos",
            "--topic",
            topic,
            "--owner",
            owner,
            "--limit",
            "1000",
            "--json",
            "url",
        ]
        result = json.loads(make_gh_request(request))
        for t in result:
            repos[t["url"]] = {"trusted": True}
        print(f"      Found {len(result)} repository/repositories for '{owner}'.")
    return repos


def retrieve_repos_from_file(filename: str) -> list[str]:
    with open(filename, "r") as f:
        repos = yaml.safe_load(f)
        for repo_url, repo_info in repos.items():
            repo_info["trusted"] = repo_info.get("trusted", False)
        return repos


def retrieve_multiple_repos_graphql(repos: dict):
    repo_queries = []
    for repo_url, repo_info in repos.items():
        # Expecting format https://github.com/owner/name
        owner_name_part = repo_url.rstrip("/").split("github.com/")[-1]
        owner, name = owner_name_part.split("/")
        alias = re.sub(r"[^a-zA-Z0-9_]", "_", owner_name_part)
        repo_query = f"""
            {alias}: repository(owner: "{owner}", name: "{name}") {{
                name
                nameWithOwner
                description
                openGraphImageUrl
                createdAt
                stargazerCount
                repositoryTopics(first: 10) {{
                    nodes {{
                        topic {{
                            name
                        }}
                    }}
                }}
                pullRequests {{
                    totalCount
                }}
                defaultBranchRef {{
                    target {{
                        ... on Commit {{
                            history(first: 1) {{
                                totalCount
                                nodes {{
                                    committedDate
                                }}
                            }}
                        }}
                    }}
                }}
            }}"""
        repo_queries.append(repo_query)
    query = f"""
        query {{
            {",".join(repo_queries)}
        }}
    """.strip()
    mini_query = minify_graphql(query)
    cmd = ["gh", "api", "graphql", "-f", f"query={mini_query}"]
    data = json.loads(make_gh_request(cmd))["data"]
    return data


def is_gh_url(url):
    return url.startswith("https://github.com/")


def repos_data_to_json(repos_data):
    fetched_repos = {}
    for repo_data in repos_data.values():
        topics = [
            node["topic"]["name"] for node in repo_data["repositoryTopics"]["nodes"]
        ]
        url = f"https://github.com/{repo_data['nameWithOwner']}"

        commit_count = 0
        last_commit_date = None
        if repo_data["defaultBranchRef"]:
            history = repo_data["defaultBranchRef"]["target"]["history"]
            commit_count = history["totalCount"]
            if history.get("nodes"):
                last_commit_date = history["nodes"][0]["committedDate"]
        else:
            warnings.warn(f"{url} has no default branch. Is it empty ?")
            continue

        fetched_repos[url] = {
            "name": repo_data["name"],
            "description": repo_data["description"] or "",
            "image": repo_data["openGraphImageUrl"],
            "topics": topics,
            "createdAt": repo_data["createdAt"],
            "lastCommitDate": last_commit_date,
            "stars": repo_data["stargazerCount"],
            "commitCount": commit_count,
            "pullRequestCount": repo_data["pullRequests"]["totalCount"],
        }
    return fetched_repos


def fetch_gh_info(gh_repos):
    repos_data = retrieve_multiple_repos_graphql(gh_repos)
    json_repos_info = repos_data_to_json(repos_data)

    fetched_repos_info = {}
    for url, repo_info in gh_repos.items():
        if url in json_repos_info:
            fetched_repos_info[url] = json_repos_info[url] | repo_info
        else:
            warnings.warn(
                f"The fetched github repository has a different URL than the one provided. "
                f"Check that the repository URL in `extrernal_repos.yml` isn't an alias: {url}."
            )
    return fetched_repos_info


def add_info(repos):
    one_year_ago = datetime.now(timezone.utc) - timedelta(days=365)
    for url, repo_info in repos.items():
        if not repo_info["trusted"]:
            repo_info["topics"].append("...")

        created_at = datetime.fromisoformat(
            repo_info["createdAt"].replace("Z", "+00:00")
        )
        repo_info["createdWithinLastYear"] = created_at >= one_year_ago


if __name__ == "__main__":
    print("- Discovering trusted repositories from GitHub...")
    trusted_repos = retrieve_gh_repos_from_topic("trame", ["Kitware", "KitwareMedical"])

    print(f"- Loading external repositories from '{EXTERNAL_REPOS_FILE}'...")
    external_repos = retrieve_repos_from_file(EXTERNAL_REPOS_FILE)

    print("    Trusted repos:")
    for repo in trusted_repos:
        print(f"      {repo}")
    print("    External repos:")
    for repo in external_repos:
        print(f"      {repo}")

    repos = trusted_repos | external_repos

    gh_repos = {k: v for k, v in repos.items() if is_gh_url(k)}
    non_gh_repos = {k: v for k, v in repos.items() if not is_gh_url(k)}

    print(f"- Fetching GraphQL metadata for {len(gh_repos)} GitHub repositories...")
    fetched_gh_repos = fetch_gh_info(gh_repos)

    print("- Enriching repository information and calculating age...")
    repos_w_info = non_gh_repos | fetched_gh_repos
    add_info(repos_w_info)

    print(f"- Writing combined results to '{OUTPUT_FILE}'...")
    with open(OUTPUT_FILE, "w") as f:
        json.dump(
            repos_w_info,
            f,
            sort_keys=True,
        )
    print("Done")
