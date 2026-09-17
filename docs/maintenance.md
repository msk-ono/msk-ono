# Maintaining the profile

The profile uses GitHub-rendered Markdown and self-contained SVG images. No
third-party statistics server, personal access token, or Python packages are
required. All displayed activity is public.

## Updating the design

The mathematical illustration and the activity panel share the palette in
`scripts/profile_assets.py`. Regenerate the headers after editing it:

```sh
python3 scripts/profile_assets.py
```

The header draws contours of a positive-definite quadratic and an actual
gradient-descent trajectory. The README selects an animation-free SVG when the
viewer requests reduced motion, since image-embedded SVGs do not consistently
inherit that preference. GitHub's `<picture>` support selects the light
or dark asset. Below a 600px viewport width, the activity panel switches to a
compact version with legible labels. Descriptive text and links remain in Markdown.

## Activity refresh

The **Refresh public activity** workflow runs every six hours, at minute 17 UTC,
on changes to the updater/profile on `main`, and through **Run workflow** in the
Actions tab. Its first successful run refreshes the included public snapshot
(or replaces the placeholder if the snapshot has not been generated yet).
GitHub can delay scheduled jobs; the README shows the snapshot's UTC timestamp.

The workflow uses its automatically provided `GITHUB_TOKEN` with `contents: write`
to commit generated files as `github-actions[bot]`. It does not need a custom
secret. If repository rules prevent direct bot pushes to `main`, the workflow
will fail visibly instead of bypassing those rules. Updates made with this token
do not recursively trigger the push workflow.

For an explicit local refresh using the existing GitHub CLI login:

```sh
python3 scripts/update_activity.py --use-gh-auth --dry-run
python3 scripts/update_activity.py --use-gh-auth
```

Alternatively, provide `GITHUB_TOKEN` through the environment. Never commit a
token. Without credentials the updater exits without changing files.

### What the counts mean

The reporting window is the preceding 30 days ending at the snapshot timestamp.
GraphQL `contributionsCollection` provides the counts. Repository-level results
are filtered to public repositories, even if the local token can see private
ones; anonymous private contribution totals are never used.

- **Commits:** commit contributions, not push events. GitHub's eligibility rules
  apply, so fork-only and non-default-branch commits may not count.
- **Pull requests:** PRs opened during the reporting window, not PR updates or
  merges counted a second time.
- **Reviews:** GitHub review contributions; these are not individual review
  comments or every repeated submission on the same PR.

See [GitHub's contribution rules](https://docs.github.com/en/account-and-profile/reference/profile-contributions-reference)
and the [GraphQL reference](https://docs.github.com/en/graphql/reference/users#contributionscollection).

### What the feed means

The public events endpoint exposes at most 300 events from the last 30 days and
can lag by several hours. The updater sorts the available events by timestamp,
removes duplicates, and shows at most five pushes, PR actions, or reviews by
`msk-ono`. Stars and branch creation are omitted. A push links to its head commit;
it does not claim that every commit in that push was authored by the user.
PR openings and merges are separate historical events. The feed can therefore
contain activity that does not qualify for the contribution counts above.

Repository visibility is checked again before enriching events with titles.
Titles are shortened and escaped before rendering. A valid empty feed displays
an empty-state sentence; failed requests are never interpreted as zero activity.

### Failure behavior

The updater finishes data collection and rendering before writing any files.
GraphQL partial errors, truncated contribution collections, rate limits, or
failed event detail requests fail the workflow and preserve the previous
snapshot. Each generated file is replaced atomically. No partial result is
committed if the update command fails.

The README's `ACTIVITY:START` / `ACTIVITY:END` markers delimit the only text the
updater replaces. Keep exactly one pair, in that order. Generated SVG files and
`assets/activity.json` should not be edited manually.

GitHub can disable scheduled workflows after 60 days without repository activity.
If refreshes stop, check the Actions tab and re-enable the workflow. See
[GitHub's workflow guidance](https://docs.github.com/en/actions/managing-workflow-runs-and-deployments/managing-workflow-runs/disabling-and-enabling-a-workflow).

## Checks

```sh
python3 -m unittest discover -s tests -v
```

Also preview the GitHub-rendered README in light/dark themes at desktop and phone
widths. Verify both normal motion and reduced motion. Local browser previews
approximate GitHub layout; confirm image paths and theme selection on GitHub
after the first push.
