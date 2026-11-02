# Changelog fragments

Do not edit `CHANGELOG.md` directly in a pull request: every PR used to
append at the same `## [Unreleased]` anchor, so open PRs conflicted with
each other on that file alone (issue #148). Instead, add one fragment
file per PR here.

## Format

- Filename: `<issue-or-PR-number>.md`, for example `148.md`. One file per
  PR; two PRs therefore never touch the same file.
- Content: Keep a Changelog sections (`### Added`, `### Changed`,
  `### Deprecated`, `### Removed`, `### Fixed`, `### Security`), each
  followed by bullet lines. Every bullet ends with its PR link, for
  example `- Slimming no longer drops a keyword (#148).`
- A fragment with no section, a bullet outside a section, or an unknown
  section fails validation (`python3 .github/scripts/changelog_fragments.py check`).

## Folding

The maintainer release PR folds fragments into `CHANGELOG.md` before
moving `Unreleased` under the new version, exactly as before:

```bash
python3 .github/scripts/changelog_fragments.py fold
```

Folding inserts the entries under `## [Unreleased]` (existing entries
stay first, new sections are added in Keep a Changelog order) and deletes
the fragment files. The release workflow is unchanged: it still reads its
notes from the `CHANGELOG.md` version section.
