"""Changelog fragments: one file per PR, folded into CHANGELOG.md at release.

Why this exists: every PR used to append its entry at the top of the
``## [Unreleased]`` section of CHANGELOG.md, so any two open PRs conflicted
with each other on that file alone (see issue #148). ``CHANGELOG.md
merge=union`` in ``.gitattributes`` does not fix that: GitHub computes PR
mergeability server-side and does not run merge drivers from
``.gitattributes``, so the PRs still read CONFLICTING. One fragment file per
PR removes the shared lines entirely: two PRs touch different files, so
they cannot conflict on the changelog.

Format (see ``changelog.d/README.md``): a fragment is
``changelog.d/<issue-or-PR-number>.md``. Its content is Keep a Changelog
sections (``### Added``, ``### Fixed``, ...) each followed by bullet lines.
Example::

    ### Fixed
    - Slimming no longer drops a keyword guarded by unevaluatedProperties (#148).

Commands (run from the repository root)::

    python3 .github/scripts/changelog_fragments.py check   # validate fragments
    python3 .github/scripts/changelog_fragments.py render  # print the folded section
    python3 .github/scripts/changelog_fragments.py fold    # fold into CHANGELOG.md
                                                            # and delete fragments

``fold`` is for the maintainer release PR: it moves fragment entries under
``## [Unreleased]``, preserving any entries already there, and deletes the
fragment files. The release workflow is unchanged: it still reads its notes
from the CHANGELOG.md version section, which keeps exactly the current
format once the release PR moves ``Unreleased`` under the new version.

The script itself uses the standard library only. Its behaviour is tested
in ``test_changelog_fragments.py`` (``uv run pytest .github/scripts``).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

SECTION_ORDER = ("Added", "Changed", "Deprecated", "Removed", "Fixed", "Security")
SECTION_RE = re.compile(r"^###\s+(Added|Changed|Deprecated|Removed|Fixed|Security)\s*$")
BULLET_RE = re.compile(r"^-\s+\S")
FRAGMENT_RE = re.compile(r"^(\d+)\.md$")
UNRELEASED_RE = re.compile(r"^## \[Unreleased\]\s*$")
HEADING_RE = re.compile(r"^##\s")


class FragmentError(ValueError):
    """A fragment file (or the changelog layout) is malformed."""


def parse_fragment(text: str, name: str) -> dict[str, list[str]]:
    """Parse one fragment file into ``{section: [bullet, ...]}``.

    Raises FragmentError on an empty file, a bullet outside a section,
    an unknown section heading, or any other non-bullet, non-heading line.
    """
    sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        match = SECTION_RE.match(stripped)
        if match:
            current = match.group(1)
            sections.setdefault(current, [])
            continue
        if stripped.startswith("###"):
            raise FragmentError(f"{name}: unknown section heading: {stripped!r}")
        if BULLET_RE.match(stripped):
            if current is None:
                raise FragmentError(f"{name}: bullet before any section heading")
            sections[current].append(stripped)
            continue
        raise FragmentError(f"{name}: unexpected line: {stripped!r}")
    if not sections or all(not bullets for bullets in sections.values()):
        raise FragmentError(f"{name}: fragment is empty")
    return sections


def collect_fragments(changelog_dir: Path) -> list[tuple[int, dict[str, list[str]]]]:
    """Read and validate every fragment, sorted by number.

    Returns ``[(number, sections), ...]``. Raises FragmentError on a
    misnamed file or malformed content. A missing directory means no
    fragments, not an error.
    """
    if not changelog_dir.is_dir():
        return []
    collected = []
    for path in sorted(changelog_dir.iterdir()):
        if path.name == "README.md" or path.is_dir():
            continue
        match = FRAGMENT_RE.match(path.name)
        if not match:
            raise FragmentError(f"{path.name}: fragment files must be named <number>.md")
        collected.append(
            (int(match.group(1)), parse_fragment(path.read_text(encoding="utf-8"), path.name))
        )
    collected.sort(key=lambda item: item[0])
    return collected


def validate(root: Path) -> list[str]:
    """Return a list of validation errors (empty when fragments are fine)."""
    try:
        collect_fragments(root / "changelog.d")
    except FragmentError as exc:
        return [str(exc)]
    return []


def render_unreleased(
    existing: list[str], fragments: list[tuple[int, dict[str, list[str]]]]
) -> list[str]:
    """Merge fragment bullets into the existing Unreleased body lines.

    Existing section blocks keep their bullets first; fragment bullets are
    appended in fragment-number order. Sections that exist only in
    fragments are added at the end, in Keep a Changelog order. Non-section
    prose lines already under Unreleased are preserved at the top.
    """
    prose: list[str] = []
    existing_sections: dict[str, list[str]] = {}
    current: str | None = None
    for line in existing:
        stripped = line.strip()
        match = SECTION_RE.match(stripped)
        if match:
            current = match.group(1)
            existing_sections.setdefault(current, [])
            continue
        if stripped.startswith("###"):
            current = None
            prose.append(line)
            continue
        if current is not None and BULLET_RE.match(stripped):
            existing_sections[current].append(stripped)
        elif stripped:
            prose.append(line)
    merged: dict[str, list[str]] = {
        section: list(bullets) for section, bullets in existing_sections.items()
    }
    for _number, sections in fragments:
        for section, bullets in sections.items():
            merged.setdefault(section, []).extend(bullets)
    out = list(prose)
    for section in SECTION_ORDER:
        bullets = merged.get(section)
        if not bullets:
            continue
        if out:
            out.append("")
        out.append(f"### {section}")
        out.append("")
        out.extend(bullets)
    return out


def fold_changelog(changelog_path: Path, changelog_dir: Path) -> list[str]:
    """Fold fragments into CHANGELOG.md under ``## [Unreleased]``.

    Returns the list of fragment filenames that were folded (and deleted).
    Raises FragmentError when the changelog has no Unreleased section or
    when a fragment is malformed. With no fragments this is a no-op.
    """
    fragments = collect_fragments(changelog_dir)
    if not fragments:
        return []
    text = changelog_path.read_text(encoding="utf-8")
    lines = text.splitlines()
    start = next((i for i, line in enumerate(lines) if UNRELEASED_RE.match(line.strip())), None)
    if start is None:
        raise FragmentError("CHANGELOG.md has no '## [Unreleased]' section")
    end = next((i for i in range(start + 1, len(lines)) if HEADING_RE.match(lines[i])), len(lines))
    body = [line for line in lines[start + 1 : end] if line.strip()]
    folded = render_unreleased(body, fragments)
    new_lines = lines[: start + 1] + (["", *folded] if folded else []) + lines[end:]
    changelog_path.write_text("\n".join(new_lines).rstrip() + "\n", encoding="utf-8")
    folded_names = []
    for number, _sections in fragments:
        path = changelog_dir / f"{number}.md"
        path.unlink()
        folded_names.append(path.name)
    return folded_names


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("command", choices=("check", "render", "fold"))
    parser.add_argument("root", nargs="?", type=Path, default=Path(__file__).resolve().parents[2])
    args = parser.parse_args(argv)
    root: Path = args.root
    changelog_dir = root / "changelog.d"
    if args.command == "check":
        errors = validate(root)
        if errors:
            for error in errors:
                print(f"error: {error}", file=sys.stderr)
            return 1
        print("changelog fragments: ok")
        return 0
    try:
        fragments = collect_fragments(changelog_dir)
    except FragmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if args.command == "render":
        lines = (root / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
        start = next((i for i, line in enumerate(lines) if UNRELEASED_RE.match(line.strip())), None)
        existing: list[str] = []
        if start is not None:
            end = next(
                (i for i in range(start + 1, len(lines)) if HEADING_RE.match(lines[i])),
                len(lines),
            )
            existing = [line for line in lines[start + 1 : end] if line.strip()]
        print("## [Unreleased]")
        for line in render_unreleased(existing, fragments):
            print(line)
        return 0
    try:
        folded = fold_changelog(root / "CHANGELOG.md", changelog_dir)
    except FragmentError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    if folded:
        print(f"folded {len(folded)} fragment(s): {', '.join(folded)}")
    else:
        print("no fragments to fold")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
