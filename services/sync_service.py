from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from services.db_service import DatabaseService
from services.errors import CloudConflict, CloudError


@dataclass(frozen=True)
class SyncResult:
    pushed: int
    conflicts: int
    pending: int
    failed: int
    cloud_revision: int


class SyncService:
    def __init__(
        self, local_db: DatabaseService, cloud_db: Any
    ) -> None:
        self.local_db = local_db
        self.cloud_db = cloud_db

    def sync(self, user_id: str, church_id: str) -> SyncResult:
        pushed = 0
        conflicts = 0
        for operation in self.local_db.get_pending_operations(church_id):
            try:
                remote_record = self.cloud_db.push_operation(
                    user_id, church_id, operation
                )
            except CloudConflict as exc:
                self.local_db.mark_conflict(operation["operation_id"], str(exc))
                conflicts += 1
            except CloudError as exc:
                self.local_db.mark_operation_error(
                    operation["operation_id"], str(exc)
                )
                raise
            else:
                self.local_db.complete_operation(operation, remote_record)
                pushed += 1

        snapshot = self.cloud_db.pull_snapshot(user_id, church_id)
        cloud_revision = int(snapshot.pop("revision", 0))
        self.local_db.apply_remote_snapshot(church_id, snapshot)
        counts = self.local_db.get_sync_counts(church_id)
        return SyncResult(
            pushed=pushed,
            conflicts=conflicts,
            pending=counts["pending"],
            failed=counts["failed"],
            cloud_revision=cloud_revision,
        )
