"""Guard the convention: code lives in ``src/``, skills are documentation only."""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"
SRC_PATH_RE = re.compile(r"\bsrc/[\w/]+\.py\b")


def test_skill_md_src_paths_exist() -> None:
    """Every ``src/<name>.py`` mentioned in a SKILL.md must exist."""
    missing = [
        f"{skill_md.parent.name}: {path}"
        for skill_md in sorted(SKILLS_DIR.glob("*/SKILL.md"))
        for path in sorted(set(SRC_PATH_RE.findall(skill_md.read_text(encoding="utf-8"))))
        if not (REPO_ROOT / path).is_file()
    ]
    assert not missing, f"SKILL.md files reference missing scripts: {missing}"


def test_no_python_files_under_skills() -> None:
    """Skills hold knowledge only; scripts belong in ``src/``."""
    stray = sorted(str(p.relative_to(REPO_ROOT)) for p in SKILLS_DIR.rglob("*.py"))
    assert not stray, f"Move these into src/ and reference them from SKILL.md: {stray}"
