#!/usr/bin/env python3
"""Generate a public-only activity snapshot and update the profile atomically.

Python standard library only. CI uses GITHUB_TOKEN. For local regeneration,
pass --use-gh-auth to explicitly use the authenticated GitHub CLI account.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import html
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

from profile_assets import render_stats

ROOT = Path(__file__).resolve().parents[1]
USERNAME = "msk-ono"
START = "<!-- ACTIVITY:START -->"
END = "<!-- ACTIVITY:END -->"

QUERY = """
query($login: String!, $from: DateTime!, $to: DateTime!) {
  user(login: $login) {
    contributionsCollection(from: $from, to: $to) {
      totalRepositoriesWithContributedCommits
      totalRepositoriesWithContributedPullRequests
      totalRepositoriesWithContributedPullRequestReviews
      commitContributionsByRepository(maxRepositories: 100) {
        repository { isPrivate }
        contributions(first: 100) {
          nodes { commitCount }
          pageInfo { hasNextPage }
        }
      }
      pullRequestContributionsByRepository(maxRepositories: 100) {
        repository { isPrivate }
        contributions { totalCount }
      }
      pullRequestReviewContributionsByRepository(maxRepositories: 100) {
        repository { isPrivate }
        contributions { totalCount }
      }
    }
  }
}
"""


class APIError(RuntimeError):
    """The snapshot is incomplete and must not be published."""


class GitHub:
    def __init__(self, token: str):
        self.token = token

    def request(self, path: str, body: dict | None = None):
        # Only fixed api.github.com paths are used; never follow payload API URLs.
        request = Request(
            "https://api.github.com/" + path.lstrip("/"),
            data=json.dumps(body).encode() if body is not None else None,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/vnd.github+json",
                "Content-Type": "application/json",
                "X-GitHub-Api-Version": "2022-11-28",
                "User-Agent": "msk-ono-profile",
            },
        )
        for attempt in range(3):
            try:
                with urlopen(request, timeout=30) as response:
                    return json.load(response)
            except HTTPError as error:
                if error.code in (429, 500, 502, 503, 504) and attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise APIError(f"GitHub returned HTTP {error.code} for {path}") from None
            except (URLError, TimeoutError):
                if attempt < 2:
                    time.sleep(2 ** (attempt + 1))
                    continue
                raise APIError("Could not reach GitHub; keeping the previous snapshot") from None
        raise APIError("GitHub request did not complete")


def timestamp(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def collect_counts(api: GitHub, start: datetime, end: datetime) -> dict[str, int]:
    result = api.request("graphql", {
        "query": QUERY,
        "variables": {"login": USERNAME, "from": timestamp(start), "to": timestamp(end)},
    })
    if result.get("errors"):
        raise APIError("GitHub GraphQL returned errors; refusing partial counts")
    collection = result["data"]["user"]["contributionsCollection"]
    counts = {}
    for key, group, total in (
        ("commits", "commitContributionsByRepository", "totalRepositoriesWithContributedCommits"),
        ("prs", "pullRequestContributionsByRepository", "totalRepositoriesWithContributedPullRequests"),
        ("reviews", "pullRequestReviewContributionsByRepository", "totalRepositoriesWithContributedPullRequestReviews"),
    ):
        repositories = collection[group]
        if len(repositories) < collection[total]:
            raise APIError("Contribution repository limit reached; refusing partial counts")
        count = 0
        for repository in repositories:
            # Explicitly filter even when a local token can read private repos.
            if repository["repository"]["isPrivate"]:
                continue
            contributions = repository["contributions"]
            if key == "commits":
                if contributions["pageInfo"]["hasNextPage"]:
                    raise APIError("Commit contribution days were truncated")
                count += sum(day["commitCount"] for day in contributions["nodes"])
            else:
                count += contributions["totalCount"]
        counts[key] = count
    return counts


def event_identity(event: dict) -> tuple | None:
    payload = event["payload"]
    repo = event["repo"]["name"]
    kind = event["type"]
    if kind == "PushEvent" and payload.get("head"):
        if set(payload["head"]) == {"0"}:
            return None
        return (kind, repo, payload["head"], payload.get("ref"))
    if kind == "PullRequestEvent":
        action = payload.get("action")
        if action not in ("opened", "reopened", "closed", "merged"):
            return None
        pull = payload.get("pull_request", {})
        if action == "closed" and pull.get("merged"):
            action = "merged"
        return (kind, repo, payload.get("number") or pull.get("number"), action)
    if kind == "PullRequestReviewEvent" and payload.get("action") == "created":
        return (kind, repo, payload.get("review", {}).get("id"), event["created_at"])
    return None


def event_candidates(events: list[dict], start: datetime, end: datetime) -> list[dict]:
    result = []
    seen = set()
    for event in sorted(events, key=lambda entry: entry["created_at"], reverse=True):
        if event.get("public") is not True or event.get("actor", {}).get("login", "").lower() != USERNAME:
            continue
        if not start <= parse_time(event["created_at"]) <= end:
            continue
        identity = event_identity(event)
        if identity is None or identity in seen:
            continue
        seen.add(identity)
        result.append(event)
    return result


def one_line(value: str, limit: int = 100) -> str:
    text = " ".join(value.split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


def describe_event(api: GitHub, event: dict) -> dict:
    repo = event["repo"]["name"]
    if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise APIError("Invalid repository name in a public event")
    payload = event["payload"]
    base = f"https://github.com/{repo}"
    kind = event["type"]
    if kind == "PushEvent":
        sha = payload["head"]
        commit = api.request(f"repos/{repo}/commits/{quote(sha, safe='')}")
        branch = payload.get("ref", "").removeprefix("refs/heads/")
        label = "Pushed"
        title = one_line((commit["commit"]["message"].splitlines() or [sha[:7]])[0])
        url = f"{base}/commit/{quote(sha, safe='')}"
        detail = branch
    else:
        pull = payload.get("pull_request", {})
        number = payload.get("number") or pull["number"]
        pr = api.request(f"repos/{repo}/pulls/{int(number)}")
        title = one_line(pr["title"])
        url = f"{base}/pull/{int(number)}"
        detail = f"#{number}"
        if kind == "PullRequestReviewEvent":
            label = "Reviewed"
            review_id = payload.get("review", {}).get("id")
            if isinstance(review_id, int):
                url += f"#pullrequestreview-{review_id}"
        else:
            action = payload["action"]
            # Only enrich a CLOSED event with the merged flag. An OPENED event
            # must remain opened even if the PR has since been merged.
            merged = payload.get("pull_request", {}).get("merged", pr.get("merged", False))
            if action == "merged" or (action == "closed" and merged):
                label = "Merged"
            else:
                label = {"opened": "Opened", "reopened": "Reopened", "closed": "Closed"}[action]
    return {"date": event["created_at"], "kind": label, "repo": repo,
            "title": title, "detail": one_line(detail, 60), "url": url}


def collect_activity(api: GitHub, start: datetime, end: datetime) -> list[dict]:
    events = []
    # Events can arrive out of order, so sort after collecting all available pages.
    for page in range(1, 4):
        batch = api.request(f"users/{USERNAME}/events/public?per_page=100&page={page}")
        events.extend(batch)
        if len(batch) < 100:
            break
    activity = []
    visibility = {}
    for event in event_candidates(events, start, end):
        repo = event["repo"]["name"]
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
            raise APIError("Invalid repository name in a public event")
        if repo not in visibility:
            # A once-public event may now point to a private repository.
            metadata = api.request(f"repos/{repo}")
            visibility[repo] = metadata["visibility"] == "public" and not metadata["private"]
        if visibility[repo]:
            activity.append(describe_event(api, event))
        if len(activity) == 5:
            break
    return activity


def escape_markdown(value: str) -> str:
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", html.escape(value, quote=False))


def activity_block(snapshot: dict) -> str:
    counts = snapshot["counts"]
    alt = f"Last 30 days: {counts['commits']} commits, {counts['prs']} pull requests, {counts['reviews']} reviews"
    lines = [
        '<picture>',
        '  <source media="(max-width: 600px) and (prefers-color-scheme: dark)" srcset="assets/activity-mobile-dark.svg">',
        '  <source media="(max-width: 600px)" srcset="assets/activity-mobile-light.svg">',
        '  <source media="(prefers-color-scheme: dark)" srcset="assets/activity-dark.svg">',
        f'  <img src="assets/activity-light.svg" alt="{alt}" width="1200">',
        '</picture>',
        '',
        '**Recent activity**',
        '',
    ]
    for entry in snapshot["activity"]:
        date = parse_time(entry["date"]).strftime("%b %d")
        label = escape_markdown(entry["title"])
        repo = escape_markdown(entry["repo"])
        detail = escape_markdown(entry["detail"])
        lines.append(f"- `{date}` **{entry['kind']}** [{label}]({entry['url']}) — {repo} · {detail}")
    if not snapshot["activity"]:
        lines.append("No recent public activity in the available event feed.")
    updated = parse_time(snapshot["updated_at"]).strftime("%Y-%m-%d %H:%M UTC")
    lines += [
        '',
        f'<sub>Public activity · Last 30 days · Updated {updated}</sub>',
        '',
        '<sub>[About these counts](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference)</sub>',
    ]
    return "\n".join(lines)


def replace_block(readme: str, body: str) -> str:
    if readme.count(START) != 1 or readme.count(END) != 1:
        raise ValueError("README must contain exactly one pair of activity markers")
    before, rest = readme.split(START)
    _, after = rest.split(END)
    return before + START + "\n" + body + "\n" + END + after


def build_outputs(root: Path, snapshot: dict) -> dict[Path, str]:
    return {
        root / "README.md": replace_block((root / "README.md").read_text(), activity_block(snapshot)),
        root / "assets/activity-light.svg": render_stats(snapshot, "light"),
        root / "assets/activity-dark.svg": render_stats(snapshot, "dark"),
        root / "assets/activity-mobile-light.svg": render_stats(snapshot, "light", mobile=True),
        root / "assets/activity-mobile-dark.svg": render_stats(snapshot, "dark", mobile=True),
        root / "assets/activity.json": json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n",
    }


def write_outputs(outputs: dict[Path, str]) -> None:
    # API collection and rendering finish before touching existing files.
    for path, content in outputs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_text() == content:
            continue
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as file:
            file.write(content)
            temporary = Path(file.name)
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)


def update(api: GitHub, root: Path, now: datetime, dry_run: bool = False) -> dict:
    start = now - timedelta(days=30)
    snapshot = {
        "username": USERNAME,
        "updated_at": timestamp(now),
        "period": {"from": timestamp(start), "to": timestamp(now)},
        "counts": collect_counts(api, start, now),
        "activity": collect_activity(api, start, now),
    }
    outputs = build_outputs(root, snapshot)
    if not dry_run:
        write_outputs(outputs)
    return snapshot


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="Fetch and validate without writing files")
    parser.add_argument("--use-gh-auth", action="store_true", help="Explicitly use the local gh login")
    args = parser.parse_args()
    token = os.environ.get("GITHUB_TOKEN", "")
    try:
        if args.use_gh_auth:
            token = subprocess.run(["gh", "auth", "token"], check=True, text=True, capture_output=True).stdout.strip()
        if not token:
            raise APIError("Set GITHUB_TOKEN or explicitly pass --use-gh-auth")
        snapshot = update(GitHub(token), ROOT, datetime.now(timezone.utc), args.dry_run)
    except (APIError, KeyError, TypeError, ValueError, OSError, subprocess.CalledProcessError) as error:
        # Never include token values or raw API response bodies in workflow logs.
        print(f"Activity update failed ({type(error).__name__}). Previous files were kept.", file=sys.stderr)
        if isinstance(error, (APIError, ValueError)):
            print(str(error), file=sys.stderr)
        return 1
    print(json.dumps({"counts": snapshot["counts"], "recent_events": len(snapshot["activity"]), "dry_run": args.dry_run}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
