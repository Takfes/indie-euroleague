"""Dataset/script registry derived from the pipeline-script I/O headers.

`Registry.from_repo()` reads every header (via `ast`, never importing scripts) and exposes scripts,
datasets, paths, stages and lineage edges. CLI: `uv run python -m eupy.registry check|show`.
"""

from eupy.registry.headers import Header, HeaderError
from eupy.registry.model import IN_PLACE_ALLOWLIST, Dataset, Registry, RegistryError, Script, Source

__all__ = [
    "IN_PLACE_ALLOWLIST",
    "Dataset",
    "Header",
    "HeaderError",
    "Registry",
    "RegistryError",
    "Script",
    "Source",
]
