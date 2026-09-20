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
    stage_publisher = StagePublisher(
        local_db.get_setting("render_server_url", config.render_server_url) or "",
        local_db.get_setting("render_control_token", config.render_control_token) or "",
    )
    app = App(auth_service, local_db, cloud_db, sync_service, stage_publisher)
    app.mainloop()


if __name__ == "__main__":
    main()
