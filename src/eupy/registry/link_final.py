"""Create/refresh the relative symlinks in `data/stage_99/` for every `Final: true` dataset (registry-derived).

The link set is the registry's produced datasets with `final` set; a link is named after its dataset file and
points (relative) at the real file, e.g. `data/stage_99/schedule.csv -> ../stage_01/schedule.csv`.

* A final dataset whose file does not exist yet gets no link (never dangling); it is reported as skipped.
* Stale links -- symlinks into `../stage_*/` in `data/stage_99/` that are not in the final set -- are removed;
  other symlinks are left alone.
* Regular files and directories in `data/stage_99/` are never touched (a clash with a final name is reported).
* Idempotent: a correct link is left alone, so a second run changes nothing.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from eupy.registry.model import Registry, RegistryError

LINK_DIR = "data/stage_99"


@dataclass
class LinkReport:
    """What `link_final` did, as sorted file-name lists."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)  # target missing or name taken by a non-symlink

    def lines(self) -> list[str]:
        """Human-readable summary, one line per action (empty when nothing happened)."""
        out = [f"linked   {LINK_DIR}/{n}" for n in self.created]
        out += [f"relinked {LINK_DIR}/{n}" for n in self.updated]
        out += [f"removed  {LINK_DIR}/{n} (stale)" for n in self.removed]
        out += [f"skipped  {n}" for n in self.skipped]
        return out


def final_targets(registry: Registry) -> dict[str, str]:
    """Link name -> repo-relative target path for every produced dataset flagged final."""
    targets: dict[str, str] = {}
    for ds in registry.datasets.values():
        if ds.final and ds.producer:
            path = ds.path.rstrip("/")
            name = Path(path).name
            if targets.get(name, path) != path:
                raise RegistryError([f"final datasets share the stage_99 link name {name!r}: {targets[name]}, {path}"])
            targets[name] = path
    return dict(sorted(targets.items()))


def link_final(registry: Registry, root: Path) -> LinkReport:
    """Bring `<root>/data/stage_99/` in line with the final datasets; returns what changed."""
    link_dir = root / LINK_DIR
    link_dir.mkdir(parents=True, exist_ok=True)
    report = LinkReport()
    targets = final_targets(registry)

    for entry in sorted(link_dir.iterdir()):
        # Managed = a symlink into a sibling `stage_*` dir (what this module creates); other symlinks stay.
        if entry.is_symlink() and entry.name not in targets and os.readlink(entry).startswith("../stage_"):
            entry.unlink()
            report.removed.append(entry.name)

    for name, target in targets.items():
        link = link_dir / name
        if not (root / target).exists():
            report.skipped.append(f"{name} (target {target} does not exist yet)")
            continue
        rel = os.path.relpath(root / target, link_dir)
        if link.is_symlink():
            if os.readlink(link) == rel:
                continue
            link.unlink()
            report.updated.append(name)
        elif link.exists():
            report.skipped.append(f"{name} (a non-symlink already exists in {LINK_DIR}/; left untouched)")
            continue
        else:
            report.created.append(name)
        link.symlink_to(rel)
    return report
