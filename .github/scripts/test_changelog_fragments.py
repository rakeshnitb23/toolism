"""Tests for changelog_fragments.py. Run with: uv run pytest .github/scripts"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import changelog_fragments as cf


def _write_changelog(tmp_path: Path, unreleased_body: str = "") -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(
        "# Changelog\n\n## [Unreleased]\n"
        + unreleased_body
        + "\n## [0.2.0] - 2026-10-31\n\n- Old entry.\n",
        encoding="utf-8",
    )
    return path


def _write_fragment(tmp_path: Path, name: str, text: str) -> Path:
    changelog_dir = tmp_path / "changelog.d"
    changelog_dir.mkdir(exist_ok=True)
    path = changelog_dir / name
    path.write_text(text, encoding="utf-8")
    return path


def test_parse_fragment_groups_by_section() -> None:
    sections = cf.parse_fragment(
        "### Fixed\n- A fix (#1).\n\n### Added\n- A feature (#1).\n", "1.md"
    )
    assert sections == {"Fixed": ["- A fix (#1)."], "Added": ["- A feature (#1)."]}


def test_parse_fragment_rejects_bullet_before_section() -> None:
    with pytest.raises(cf.FragmentError):
        cf.parse_fragment("- Orphan bullet.\n", "1.md")


def test_parse_fragment_rejects_unknown_section_and_empty_file() -> None:
    with pytest.raises(cf.FragmentError):
        cf.parse_fragment("### Misc\n- Something.\n", "1.md")
    with pytest.raises(cf.FragmentError):
        cf.parse_fragment("### Fixed\n", "1.md")


def test_collect_sorted_and_misnamed(tmp_path: Path) -> None:
    _write_fragment(tmp_path, "202.md", "### Fixed\n- Second (#202).\n")
    _write_fragment(tmp_path, "201.md", "### Added\n- First (#201).\n")
    collected = cf.collect_fragments(tmp_path / "changelog.d")
    assert [number for number, _ in collected] == [201, 202]
    _write_fragment(tmp_path, "some-slug.md", "### Fixed\n- Bad name.\n")
    with pytest.raises(cf.FragmentError):
        cf.collect_fragments(tmp_path / "changelog.d")


def test_fold_merges_sections_in_keep_a_changelog_order(tmp_path: Path) -> None:
    changelog = _write_changelog(tmp_path)
    _write_fragment(tmp_path, "202.md", "### Fixed\n- Second fix (#202).\n")
    _write_fragment(tmp_path, "201.md", "### Added\n- First feature (#201).\n")
    folded = cf.fold_changelog(changelog, tmp_path / "changelog.d")
    assert folded == ["201.md", "202.md"]
    text = changelog.read_text(encoding="utf-8")
    assert text.index("### Added") < text.index("### Fixed")
    assert "- First feature (#201)." in text
    assert "- Second fix (#202)." in text
    assert "- Old entry." in text  # later version section untouched
    assert not list((tmp_path / "changelog.d").glob("*.md"))


def test_fold_appends_to_existing_section_and_keeps_prose(tmp_path: Path) -> None:
    changelog = _write_changelog(tmp_path, "\n### Fixed\n\n- Existing fix.\n")
    _write_fragment(tmp_path, "203.md", "### Fixed\n- New fix (#203).\n")
    cf.fold_changelog(changelog, tmp_path / "changelog.d")
    text = changelog.read_text(encoding="utf-8")
    assert text.index("- Existing fix.") < text.index("- New fix (#203).")


def test_fold_without_fragments_is_a_noop(tmp_path: Path) -> None:
    changelog = _write_changelog(tmp_path)
    before = changelog.read_text(encoding="utf-8")
    assert cf.fold_changelog(changelog, tmp_path / "changelog.d") == []
    assert changelog.read_text(encoding="utf-8") == before


def test_folded_notes_still_extract_like_release_yml(tmp_path: Path) -> None:
    """The release workflow regexes the version section out of CHANGELOG.md.

    Fold fragments, move Unreleased under a version heading the way the
    release PR does, then run the release.yml extraction regex: the notes
    must contain both fragment entries.
    """
    changelog = _write_changelog(tmp_path)
    _write_fragment(tmp_path, "201.md", "### Added\n- First feature (#201).\n")
    _write_fragment(tmp_path, "202.md", "### Fixed\n- Second fix (#202).\n")
    cf.fold_changelog(changelog, tmp_path / "changelog.d")
    text = changelog.read_text(encoding="utf-8")
    text = text.replace("## [Unreleased]", "## [0.3.0] - 2026-11-02", 1)
    match = re.search(r"^## \[?0\.3\.0\]?[^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S)
    assert match is not None
    assert "- First feature (#201)." in match.group(1)
    assert "- Second fix (#202)." in match.group(1)
