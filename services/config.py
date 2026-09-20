from dataclasses import dataclass
import os
from pathlib import Path
import sys

from platformdirs import user_data_dir


# This value is intentionally bundled with Stage Cue so every installation uses
# the same Atlas cluster. A compiled desktop application cannot keep this value
# secret; rotate the Atlas user immediately if a build is shared unexpectedly.
EMBEDDED_MONGODB_URI = "mongodb+srv://stagecue_user:JvBGj1dDA7SHJJmf@stage-cue.ot2i89l.mongodb.net/?appName=Stage-Cue"
EMBEDDED_MONGODB_DATABASE = "stagecue"
DEFAULT_RENDER_SERVER_URL = "https://stage-server-k57b.onrender.com"


def bundled_resource_path(file_name: str) -> Path:
    """Locate a resource in source checkouts and PyInstaller one-file builds."""

    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / file_name
    return Path(__file__).resolve().parents[1] / file_name


@dataclass(frozen=True)
class AppConfig:
    google_client_config: Path
    local_database_path: Path
    mongodb_uri: str
    mongodb_database: str
    render_server_url: str
    render_control_token: str

    @classmethod
    def from_environment(cls) -> "AppConfig":
        data_directory = Path(user_data_dir("Stage Cue", "GJDevTech"))
        data_directory.mkdir(parents=True, exist_ok=True)

        return cls(
            google_client_config=Path(
                os.getenv(
                    "STAGE_CUE_GOOGLE_CLIENT_CONFIG",
                    str(bundled_resource_path("client_secret.json")),
                )
            ).expanduser(),
            local_database_path=data_directory / "stage_cue.sqlite3",
            mongodb_uri=EMBEDDED_MONGODB_URI,
            mongodb_database=EMBEDDED_MONGODB_DATABASE,
            render_server_url=os.getenv(
                "STAGE_CUE_RENDER_URL", DEFAULT_RENDER_SERVER_URL
            ),
            render_control_token=os.getenv("STAGE_CUE_RENDER_TOKEN", ""),
        )
