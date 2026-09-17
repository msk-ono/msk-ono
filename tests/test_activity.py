"""Regression tests for public-only data and non-destructive profile updates."""

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import update_activity as activity

NOW = datetime(2026, 9, 17, 12, tzinfo=timezone.utc)
START = NOW - timedelta(days=30)


def contributions():
    def group(private, count, commits=False):
        return {"repository": {"isPrivate": private}, "contributions": (
            {"nodes": [{"commitCount": count}], "pageInfo": {"hasNextPage": False}}
            if commits else {"totalCount": count}
        )}
    return {"data": {"user": {"contributionsCollection": {
        "totalRepositoriesWithContributedCommits": 2,
        "totalRepositoriesWithContributedPullRequests": 2,
        "totalRepositoriesWithContributedPullRequestReviews": 2,
        "commitContributionsByRepository": [group(False, 12, True), group(True, 999, True)],
        "pullRequestContributionsByRepository": [group(False, 4), group(True, 999)],
        "pullRequestReviewContributionsByRepository": [group(False, 2), group(True, 999)],
    }}}}


def event(kind="PushEvent", date="2026-09-16T12:00:00Z", **payload):
    return {"public": True, "type": kind, "created_at": date,
            "actor": {"login": "msk-ono"}, "repo": {"name": "example/repo"},
            "payload": payload or {"head": "abcd1234", "ref": "refs/heads/main"}}


class FakeAPI:
    def __init__(self, data=None, events=None):
        self.data = data if data is not None else contributions()
        self.events = events or []
        self.requests = []

    def request(self, path, body=None):
        self.requests.append((path, body))
        if path == "graphql":
            return deepcopy(self.data)
        if path.startswith("users/"):
            return deepcopy(self.events)
        if path == "repos/example/repo":
            return {"private": False, "visibility": "public"}
        if "/commits/" in path:
            return {"commit": {"message": "Improve calibration\n\nLong body"}}
        if "/pulls/" in path:
            return {"title": "A <title> [with](markup) & Unicode λ", "merged": True}
        raise AssertionError(path)


class CountsTests(unittest.TestCase):
    def test_only_public_contributions_and_exact_window(self):
        api = FakeAPI()
        self.assertEqual(activity.collect_counts(api, START, NOW), {"commits": 12, "prs": 4, "reviews": 2})
        variables = api.requests[0][1]["variables"]
        self.assertEqual(variables["from"], "2026-08-18T12:00:00Z")
        self.assertEqual(variables["to"], "2026-09-17T12:00:00Z")

    def test_zero_is_valid(self):
        data = contributions()
        collection = data["data"]["user"]["contributionsCollection"]
        for key in collection:
            collection[key] = [] if key.endswith("ByRepository") else 0
        self.assertEqual(activity.collect_counts(FakeAPI(data), START, NOW), {"commits": 0, "prs": 0, "reviews": 0})

    def test_partial_graphql_response_is_rejected(self):
        data = contributions()
        data["errors"] = [{"message": "Rate limited"}]
        with self.assertRaises(activity.APIError):
            activity.collect_counts(FakeAPI(data), START, NOW)

    def test_repository_cap_is_not_silently_undercounted(self):
        data = contributions()
        data["data"]["user"]["contributionsCollection"]["totalRepositoriesWithContributedCommits"] = 101
        with self.assertRaises(activity.APIError):
            activity.collect_counts(FakeAPI(data), START, NOW)

    def test_truncated_commit_days_are_rejected(self):
        data = contributions()
        data["data"]["user"]["contributionsCollection"]["commitContributionsByRepository"][0]["contributions"]["pageInfo"]["hasNextPage"] = True
        with self.assertRaises(activity.APIError):
            activity.collect_counts(FakeAPI(data), START, NOW)


class EventTests(unittest.TestCase):
    def test_sort_deduplicate_and_filter(self):
        older = event(date="2026-09-14T12:00:00Z", head="older", ref="refs/heads/main")
        newest = event()
        private = event(date="2026-09-17T11:00:00Z")
        private["public"] = False
        bot = event()
        bot["actor"]["login"] = "github-actions[bot]"
        too_old = event(date="2026-08-01T00:00:00Z")
        future = event(date="2026-09-18T00:00:00Z")
        ignored = event("WatchEvent", action="started")
        self.assertEqual(activity.event_candidates([older, private, newest, newest, bot, too_old, future, ignored], START, NOW), [newest, older])

    def test_push_links_to_commit_without_counting_push_as_commit(self):
        result = activity.describe_event(FakeAPI(), event())
        self.assertEqual(result["kind"], "Pushed")
        self.assertEqual(result["title"], "Improve calibration")
        self.assertTrue(result["url"].endswith("/commit/abcd1234"))

    def test_currently_merged_pr_keeps_historical_open_action(self):
        result = activity.describe_event(FakeAPI(), event("PullRequestEvent", action="opened", number=42))
        self.assertEqual(result["kind"], "Opened")
        self.assertTrue(result["url"].endswith("/pull/42"))

    def test_both_merge_event_formats(self):
        for action in ("merged", "closed"):
            result = activity.describe_event(FakeAPI(), event("PullRequestEvent", action=action, number=42))
            self.assertEqual(result["kind"], "Merged")

    def test_review_links_to_specific_review(self):
        result = activity.describe_event(FakeAPI(), event("PullRequestReviewEvent", action="created", pull_request={"number": 42}, review={"id": 123}))
        self.assertEqual(result["kind"], "Reviewed")
        self.assertTrue(result["url"].endswith("#pullrequestreview-123"))

    def test_collect_returns_at_most_five(self):
        events = [event(head=f"abcd{i}", ref="refs/heads/main") for i in range(8)]
        self.assertEqual(len(activity.collect_activity(FakeAPI(events=events), START, NOW)), 5)

    def test_formerly_public_repository_is_omitted(self):
        api = FakeAPI(events=[event()])
        request = api.request
        def now_private(path, body=None):
            if path == "repos/example/repo":
                return {"private": True, "visibility": "private"}
            return request(path, body)
        api.request = now_private
        self.assertEqual(activity.collect_activity(api, START, NOW), [])


class OutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.readme = "My biography\n" + activity.START + "\nPrevious activity\n" + activity.END + "\nMy footer\n"
        (self.root / "README.md").write_text(self.readme)

    def tearDown(self):
        self.temp.cleanup()

    def test_full_update_preserves_manual_content_and_valid_svg(self):
        snapshot = activity.update(FakeAPI(events=[event("PullRequestEvent", action="opened", number=42)]), self.root, NOW)
        readme = (self.root / "README.md").read_text()
        self.assertTrue(readme.startswith("My biography\n"))
        self.assertTrue(readme.endswith("\nMy footer\n"))
        self.assertIn("&lt;title&gt;", readme)
        self.assertIn(r"\[with\]\(markup\)", readme)
        self.assertNotIn("999", readme)
        self.assertEqual(json.loads((self.root / "assets/activity.json").read_text()), snapshot)
        for variant in ("", "mobile-"):
            for theme in ("light", "dark"):
                svg = ET.parse(self.root / f"assets/activity-{variant}{theme}.svg")
                self.assertEqual(svg.getroot().tag, "{http://www.w3.org/2000/svg}svg")

    def test_api_failure_keeps_all_existing_files(self):
        activity.update(FakeAPI(), self.root, NOW)
        original = {file: file.read_bytes() for file in self.root.rglob("*") if file.is_file()}
        with patch.object(FakeAPI, "request", side_effect=activity.APIError("HTTP 403")):
            with self.assertRaises(activity.APIError):
                activity.update(FakeAPI(), self.root, NOW)
        self.assertEqual(original, {file: file.read_bytes() for file in self.root.rglob("*") if file.is_file()})

    def test_enrichment_failure_keeps_existing_files(self):
        api = FakeAPI(events=[event()])
        request = api.request
        def fail_commit(path, body=None):
            if "/commits/" in path:
                raise activity.APIError("HTTP 404")
            return request(path, body)
        api.request = fail_commit
        with self.assertRaises(activity.APIError):
            activity.update(api, self.root, NOW)
        self.assertEqual((self.root / "README.md").read_text(), self.readme)
        self.assertFalse((self.root / "assets").exists())

    def test_empty_activity_and_dry_run(self):
        snapshot = activity.update(FakeAPI(), self.root, NOW, dry_run=True)
        self.assertIn("No recent public activity", activity.activity_block(snapshot))
        self.assertEqual((self.root / "README.md").read_text(), self.readme)
        self.assertFalse((self.root / "assets").exists())

    def test_invalid_markers_fail_before_any_write(self):
        for text in ("no markers", self.readme + activity.START, activity.END + "\n" + activity.START):
            (self.root / "README.md").write_text(text)
            with self.assertRaises(ValueError):
                activity.update(FakeAPI(), self.root, NOW)
            self.assertEqual((self.root / "README.md").read_text(), text)


if __name__ == "__main__":
    unittest.main()
