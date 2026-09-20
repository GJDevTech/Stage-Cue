"""Write build-time GitHub repository metadata without shell quoting tricks."""

from __future__ import annotations

import os
from pathlib import Path
import re


repository = os.environ.get("STAGE_CUE_REPOSITORY", "").strip()
if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repository):
    raise SystemExit("STAGE_CUE_REPOSITORY must use the owner/repository format")

destination = Path(__file__).resolve().parents[1] / "services" / "release_config.py"
destination.write_text(
    '"""Release values stamped by GitHub Actions before packaging."""\n\n'
    f'GITHUB_REPOSITORY = "{repository}"\n'
    'RELEASE_API_URL = ""\n',
    encoding="utf-8",
)
print(f"Configured updater repository: {repository}")
