from ui.app import App
from services.auth_service import AuthService
from services.cloud_service import CloudDatabaseService
from services.config import AppConfig
from services.db_service import DatabaseService
from services.sync_service import SyncService
from services.stage_publisher import StagePublisher


def main() -> None:
    config = AppConfig.from_environment()
    local_db = DatabaseService(config.local_database_path)
    cloud_db = CloudDatabaseService(
        local_db,
        config.mongodb_uri,
        config.mongodb_database,
    )
    auth_service = AuthService(config.google_client_config)
    sync_service = SyncService(local_db, cloud_db)
    # Firebase is an application-level service, not a per-church user setting.
    # The selected Stage Cue church ID chooses the permanent Stage View instance.
    stage_publisher = StagePublisher(
        config.firebase_database_url,
        config.firebase_hosting_url,
        config.firebase_api_key,
    )
    app = App(auth_service, local_db, cloud_db, sync_service, stage_publisher)
    app.mainloop()


if __name__ == "__main__":
    main()
