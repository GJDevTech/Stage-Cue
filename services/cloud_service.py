from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Iterator

from bson import ObjectId
from pymongo import MongoClient, ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.server_api import ServerApi

from services.db_service import DatabaseService, default_display_settings_bundle
from services.errors import (
    AccountNotRegistered,
    AdminRequired,
    AuthorizationError,
    CloudConfigurationError,
    CloudConflict,
    CloudError,
    CloudUnavailable,
    MembershipRequired,
)


class CloudDatabaseService:
    """Atlas accounts, memberships, church data, and synchronization."""

    def __init__(
        self,
        local_db: DatabaseService,
        mongodb_uri: str,
        database_name: str,
    ):
        self.local_db = local_db
        self.mongodb_uri = mongodb_uri.strip()
        self._database_name = database_name.strip()
        if not self.mongodb_uri.startswith(("mongodb://", "mongodb+srv://")):
            raise CloudConfigurationError(
                "The bundled MongoDB Atlas connection string is invalid."
            )
        if not self._database_name:
            raise CloudConfigurationError(
                "The bundled MongoDB database name is missing."
            )
        self._client = MongoClient(
            self.mongodb_uri,
            server_api=ServerApi("1"),
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=5000,
        )

    def database_name(self) -> str:
        return self._database_name

    def has_credentials(self) -> bool:
        return True

    def close(self) -> None:
        self._client.close()

    @contextmanager
    def _database(self) -> Iterator[Any]:
        try:
            yield self._client[self.database_name()]
        except CloudError:
            raise
        except PyMongoError as exc:
            raise CloudUnavailable(f"Atlas is unavailable: {exc}") from exc

    # ------------------------------------------------------------------
    # Accounts and church selection
    # ------------------------------------------------------------------

    def find_account(self, profile: dict[str, Any]) -> dict[str, Any] | None:
        with self._database() as database:
            user = database.users.find_one({"googleId": profile["sub"]})
            if not user:
                return None
            database.users.update_one(
                {"_id": user["_id"]},
                {
                    "$set": {
                        "email": profile["email"],
                        "emailNormalized": profile["email"].strip().lower(),
                        "lastLoginAt": datetime.now(timezone.utc),
                    }
                },
            )
            user["email"] = profile["email"]
            self._migrate_legacy_membership(database, user)
            return self._serialize_account(user)

    def create_account(
        self, profile: dict[str, Any], display_name: str
    ) -> dict[str, Any]:
        display_name = display_name.strip()
        if not display_name:
            raise ValueError("Your name is required.")
        email = profile["email"].strip()
        email_normalized = email.lower()
        with self._database() as database:
            existing = database.users.find_one({"googleId": profile["sub"]})
            if existing:
                self._migrate_legacy_membership(database, existing)
                return self._serialize_account(existing)
            email_owner = database.users.find_one(
                {
                    "$or": [
                        {"emailNormalized": email_normalized},
                        {"email": {"$regex": f"^{self._regex_escape(email)}$", "$options": "i"}},
                    ]
                }
            )
            if email_owner:
                raise AuthorizationError(
                    "An account already exists for this email using a different "
                    "Google identity."
                )
            document = {
                "googleId": profile["sub"],
                "email": email,
                "emailNormalized": email_normalized,
                "displayName": display_name,
                "createdAt": datetime.now(timezone.utc),
                "lastLoginAt": datetime.now(timezone.utc),
            }
            try:
                result = database.users.insert_one(document)
            except DuplicateKeyError as exc:
                raise AuthorizationError(
                    "This Google account or email is already registered."
                ) from exc
            document["_id"] = result.inserted_id
            return self._serialize_account(document)

    def list_church_choices(self, user_id: str) -> dict[str, list[dict[str, Any]]]:
        with self._database() as database:
            self._require_account(database, user_id)
            memberships = list(
                database.memberships.find(
                    {
                        "userId": user_id,
                        "$or": [
                            {"status": "active"},
                            {"status": {"$exists": False}},
                        ],
                    }
                )
            )
            active = []
            active_ids: set[str] = set()
            for membership in memberships:
                church_id = str(membership["churchId"])
                church = self._find_church(database, church_id)
                if not church:
                    continue
                active_ids.add(church_id)
                active.append(
                    {
                        **self._serialize_church(church),
                        "role": membership.get("role", "member"),
                    }
                )

            requests = list(
                database.membershipRequests.find(
                    {"userId": user_id, "status": "pending"}
                )
            )
            pending = []
            pending_ids: set[str] = set()
            for request in requests:
                church_id = str(request["churchId"])
                church = self._find_church(database, church_id)
                if not church:
                    continue
                pending_ids.add(church_id)
                pending.append(self._serialize_church(church))

            excluded = active_ids | pending_ids
            available = [
                self._serialize_church(church)
                for church in database.churches.find({}).sort("name", 1).limit(500)
                if str(church["_id"]) not in excluded
            ]
            active.sort(key=lambda value: value["name"].lower())
            pending.sort(key=lambda value: value["name"].lower())
            return {
                "memberships": active,
                "pending": pending,
                "available": available,
            }

    def get_session(self, user_id: str, church_id: str) -> dict[str, Any]:
        with self._database() as database:
            user = self._require_account(database, user_id)
            membership = self._require_membership(database, user_id, church_id)
            church = self._find_church(database, church_id)
            if not church:
                raise MembershipRequired("This church no longer exists.")
            return self._session_from_documents(user, church, membership)

    def resolve_session(
        self, profile: dict[str, Any], church_id: str | None = None
    ) -> dict[str, Any]:
        account = self.find_account(profile)
        if not account:
            raise AccountNotRegistered("Create your Stage Cue account first.")
        if church_id:
            return self.get_session(account["id"], church_id)
        choices = self.list_church_choices(account["id"])["memberships"]
        if len(choices) != 1:
            raise MembershipRequired("Select a church to continue.")
        return self.get_session(account["id"], choices[0]["id"])

    def create_church_with_admin(
        self, user_id: str, church_name: str, location: str
    ) -> dict[str, Any]:
        church_name = church_name.strip()
        if not church_name:
            raise ValueError("Church name is required.")
        with self._database() as database:
            user = self._require_account(database, user_id)
            church = {
                "name": church_name,
                "location": location.strip(),
                "createdByUserId": user_id,
                "createdAt": datetime.now(timezone.utc),
            }
            result = database.churches.insert_one(church)
            church["_id"] = result.inserted_id
            church_id = str(result.inserted_id)
            membership = {
                "_id": self._membership_id(church_id, user_id),
                "churchId": church_id,
                "userId": user_id,
                "role": "admin",
                "status": "active",
                "createdAt": datetime.now(timezone.utc),
                "updatedAt": datetime.now(timezone.utc),
            }
            try:
                database.memberships.insert_one(membership)
                database.displaySettings.update_one(
                    {"_id": church_id},
                    {
                        "$setOnInsert": {
                            "churchId": church_id,
                            "settings": default_display_settings_bundle(),
                            "version": 1,
                            "updatedAt": datetime.now(timezone.utc),
                        }
                    },
                    upsert=True,
                )
            except Exception:
                database.memberships.delete_one({"_id": membership["_id"]})
                database.churches.delete_one({"_id": result.inserted_id})
                raise
            return self._session_from_documents(user, church, membership)

    def request_membership(self, user_id: str, church_id: str) -> None:
        with self._database() as database:
            self._require_account(database, user_id)
            church = self._find_church(database, church_id)
            if not church:
                raise MembershipRequired("The selected church was not found.")
            existing = database.memberships.find_one(
                self._membership_filter(user_id, church_id)
            )
            if existing:
                raise AuthorizationError("You are already a member of this church.")
            now = datetime.now(timezone.utc)
            database.membershipRequests.update_one(
                {"_id": self._membership_id(church_id, user_id)},
                {
                    "$set": {
                        "churchId": church_id,
                        "userId": user_id,
                        "status": "pending",
                        "requestedAt": now,
                    },
                    "$unset": {
                        "reviewedAt": "",
                        "reviewedByUserId": "",
                    },
                },
                upsert=True,
            )

    # ------------------------------------------------------------------
    # Church administration
    # ------------------------------------------------------------------

    def list_admin_data(
        self, actor_user_id: str, church_id: str
    ) -> dict[str, list[dict[str, Any]]]:
        with self._database() as database:
            self._require_admin(database, actor_user_id, church_id)
            members = []
            for membership in database.memberships.find(
                {
                    "churchId": church_id,
                    "$or": [
                        {"status": "active"},
                        {"status": {"$exists": False}},
                    ],
                }
            ):
                user = self._find_user(database, str(membership["userId"]))
                if user:
                    members.append(
                        {
                            **self._serialize_account(user),
                            "role": membership.get("role", "member"),
                        }
                    )
            requests = []
            for request in database.membershipRequests.find(
                {"churchId": church_id, "status": "pending"}
            ):
                user = self._find_user(database, str(request["userId"]))
                if user:
                    requests.append(
                        {
                            "id": str(request["_id"]),
                            "user": self._serialize_account(user),
                            "requestedAt": self._format_datetime(
                                request.get("requestedAt")
                            ),
                        }
                    )
            members.sort(key=lambda value: value["name"].lower())
            requests.sort(key=lambda value: value["user"]["name"].lower())
            return {"members": members, "requests": requests}

    def approve_membership_request(
        self, actor_user_id: str, church_id: str, request_id: str
    ) -> None:
        with self._database() as database:
            self._require_admin(database, actor_user_id, church_id)
            request = database.membershipRequests.find_one(
                {"_id": request_id, "churchId": church_id, "status": "pending"}
            )
            if not request:
                raise MembershipRequired("That membership request is no longer pending.")
            now = datetime.now(timezone.utc)
            user_id = str(request["userId"])
            database.memberships.update_one(
                {"_id": self._membership_id(church_id, user_id)},
                {
                    "$set": {
                        "churchId": church_id,
                        "userId": user_id,
                        "role": "member",
                        "status": "active",
                        "updatedAt": now,
                    },
                    "$setOnInsert": {"createdAt": now},
                },
                upsert=True,
            )
            database.membershipRequests.update_one(
                {"_id": request_id},
                {
                    "$set": {
                        "status": "accepted",
                        "reviewedAt": now,
                        "reviewedByUserId": actor_user_id,
                    }
                },
            )

    def reject_membership_request(
        self, actor_user_id: str, church_id: str, request_id: str
    ) -> None:
        with self._database() as database:
            self._require_admin(database, actor_user_id, church_id)
            result = database.membershipRequests.update_one(
                {"_id": request_id, "churchId": church_id, "status": "pending"},
                {
                    "$set": {
                        "status": "rejected",
                        "reviewedAt": datetime.now(timezone.utc),
                        "reviewedByUserId": actor_user_id,
                    }
                },
            )
            if not result.matched_count:
                raise MembershipRequired("That membership request is no longer pending.")

    def add_member_by_email(
        self, actor_user_id: str, church_id: str, email: str
    ) -> dict[str, Any]:
        email_normalized = email.strip().lower()
        if not email_normalized:
            raise ValueError("Enter the member's email address.")
        with self._database() as database:
            self._require_admin(database, actor_user_id, church_id)
            user = database.users.find_one(
                {
                    "$or": [
                        {"emailNormalized": email_normalized},
                        {"email": {"$regex": f"^{self._regex_escape(email_normalized)}$", "$options": "i"}},
                    ]
                }
            )
            if not user:
                raise AccountNotRegistered(
                    "That email does not have a Stage Cue account yet."
                )
            user_id = str(user["_id"])
            now = datetime.now(timezone.utc)
            existing_membership = database.memberships.find_one(
                self._membership_filter(user_id, church_id)
            )
            role = (
                existing_membership.get("role", "member")
                if existing_membership
                else "member"
            )
            database.memberships.update_one(
                {"_id": self._membership_id(church_id, user_id)},
                {
                    "$set": {
                        "churchId": church_id,
                        "userId": user_id,
                        "role": role,
                        "status": "active",
                        "updatedAt": now,
                    },
                    "$setOnInsert": {"createdAt": now},
                },
                upsert=True,
            )
            database.membershipRequests.update_one(
                {"_id": self._membership_id(church_id, user_id)},
                {
                    "$set": {
                        "status": "accepted",
                        "reviewedAt": now,
                        "reviewedByUserId": actor_user_id,
                    }
                },
            )
            return {**self._serialize_account(user), "role": role}

    def set_member_admin(
        self,
        actor_user_id: str,
        church_id: str,
        target_user_id: str,
        is_admin: bool,
    ) -> None:
        with self._database() as database:
            self._require_admin(database, actor_user_id, church_id)
            target = self._require_membership(database, target_user_id, church_id)
            if not is_admin and target.get("role") == "admin":
                if self._admin_count(database, church_id) <= 1:
                    raise AuthorizationError(
                        "A church must always have at least one admin."
                    )
            database.memberships.update_one(
                {"_id": target["_id"]},
                {
                    "$set": {
                        "role": "admin" if is_admin else "member",
                        "updatedAt": datetime.now(timezone.utc),
                    }
                },
            )

    def remove_member(
        self, actor_user_id: str, church_id: str, target_user_id: str
    ) -> None:
        with self._database() as database:
            self._require_admin(database, actor_user_id, church_id)
            target = self._require_membership(database, target_user_id, church_id)
            if target.get("role") == "admin" and self._admin_count(
                database, church_id
            ) <= 1:
                raise AuthorizationError(
                    "The last church admin cannot be removed."
                )
            database.memberships.delete_one({"_id": target["_id"]})
            database.membershipRequests.update_one(
                {"_id": self._membership_id(church_id, target_user_id)},
                {
                    "$set": {
                        "status": "removed",
                        "reviewedAt": datetime.now(timezone.utc),
                        "reviewedByUserId": actor_user_id,
                    }
                },
                upsert=True,
            )

    # ------------------------------------------------------------------
    # Church-owned data and global publishing
    # ------------------------------------------------------------------

    def push_operation(
        self,
        user_id: str,
        church_id: str,
        operation: dict[str, Any],
    ) -> dict[str, Any]:
        operation_church_id = operation.get("church_id")
        if operation_church_id and operation_church_id != church_id:
            raise AuthorizationError(
                "This change belongs to a different church and was not uploaded."
            )
        collection_name = {
            "songbook": "songbooks",
            "song": "songs",
            "display_settings": "displaySettings",
        }.get(operation["entity_type"])
        if not collection_name:
            raise ValueError("Unsupported synchronization entity.")

        entity_id = operation["entity_id"]
        base_version = int(operation["base_version"])
        operation_id = operation["operation_id"]
        payload = dict(operation["payload"])
        payload.pop("id", None)
        payload.pop("churchId", None)
        payload.pop("version", None)
        payload.pop("deleted", None)
        updated_at = self._parse_datetime(payload.pop("updatedAt", None))
        deleted = operation["action"] == "delete"

        with self._database() as database:
            membership = self._require_membership(database, user_id, church_id)
            if (
                operation["entity_type"] == "display_settings"
                and membership.get("role") != "admin"
            ):
                raise AdminRequired(
                    "Only a church admin can change display settings."
                )
            if operation["entity_type"] == "song" and not deleted:
                songbook_id = payload.get("songbookId")
                if songbook_id and not database.songbooks.find_one(
                    {
                        "_id": songbook_id,
                        "churchId": church_id,
                        "deleted": {"$ne": True},
                    }
                ):
                    raise AuthorizationError(
                        "A song can only belong to a songbook in the same church."
                    )

            collection = database[collection_name]
            current = collection.find_one(
                {"_id": entity_id, "churchId": church_id}
            )
            if current and current.get("lastOperationId") == operation_id:
                self._bump_sync_revision(database, church_id)
                return self._serialize_record(current)

            replacement_fields = {
                **payload,
                "churchId": church_id,
                "deleted": deleted,
                "updatedAt": updated_at,
                "lastOperationId": operation_id,
            }

            if not current:
                if base_version != 0:
                    raise CloudConflict(
                        f"Cloud {operation['entity_type']} was removed or replaced."
                    )
                document = {
                    "_id": entity_id,
                    **replacement_fields,
                    "version": 1,
                }
                try:
                    collection.insert_one(document)
                except DuplicateKeyError as exc:
                    raise CloudConflict(
                        f"Cloud {operation['entity_type']} changed during synchronization."
                    ) from exc
                self._bump_sync_revision(database, church_id)
                return self._serialize_record(document)

            version_filter: dict[str, Any] = {"version": base_version}
            if base_version == 0:
                version_filter = {
                    "$or": [
                        {"version": 0},
                        {"version": {"$exists": False}},
                    ]
                }
            updated = collection.find_one_and_update(
                {
                    "_id": entity_id,
                    "churchId": church_id,
                    **version_filter,
                },
                {
                    "$set": replacement_fields,
                    "$inc": {"version": 1},
                },
                return_document=ReturnDocument.AFTER,
            )
            if not updated:
                latest = collection.find_one(
                    {"_id": entity_id, "churchId": church_id}
                )
                if latest and latest.get("lastOperationId") == operation_id:
                    return self._serialize_record(latest)
                raise CloudConflict(
                    f"Cloud {operation['entity_type']} has a newer version. "
                    "Your local copy was kept for manual review."
                )
            self._bump_sync_revision(database, church_id)
            return self._serialize_record(updated)

    def pull_snapshot(
        self, user_id: str, church_id: str
    ) -> dict[str, Any]:
        with self._database() as database:
            self._require_membership(database, user_id, church_id)
            global_songbooks = list(
                database.globalSongbooks.find({}).sort("name", 1).limit(5000)
            )
            global_songs = list(
                database.globalSongs.find({}).sort("title", 1).limit(10000)
            )
            self._add_source_church_names(database, global_songbooks + global_songs)
            return {
                "revision": self._combined_sync_revision(database, church_id),
                "songbooks": [
                    self._serialize_record(document)
                    for document in database.songbooks.find({"churchId": church_id})
                ],
                "songs": [
                    self._serialize_record(document)
                    for document in database.songs.find({"churchId": church_id})
                ],
                "displaySettings": [
                    self._serialize_record(document)
                    for document in database.displaySettings.find(
                        {"churchId": church_id}
                    )
                ],
                "globalSongbooks": [self._serialize_record(document) for document in global_songbooks],
                "globalSongs": [self._serialize_record(document) for document in global_songs],
            }

    def get_sync_revision(self, church_id: str) -> int:
        """Read one tiny marker; a full snapshot is pulled only when it changes."""

        with self._database() as database:
            return self._combined_sync_revision(database, church_id)

    def publish_to_global(
        self,
        actor_user_id: str,
        church_id: str,
        entity_type: str,
        entity_id: str,
    ) -> dict[str, Any]:
        source_collection_name = {
            "songbook": "songbooks",
            "song": "songs",
        }.get(entity_type)
        global_collection_name = {
            "songbook": "globalSongbooks",
            "song": "globalSongs",
        }.get(entity_type)
        if not source_collection_name or not global_collection_name:
            raise ValueError("Only songs and songbooks can be published globally.")

        with self._database() as database:
            self._require_admin(database, actor_user_id, church_id)
            church = self._find_church(database, church_id)
            source_church_name = (
                str(church.get("name", "")).strip() if church else ""
            ) or "Unknown church"
            source = database[source_collection_name].find_one(
                {"_id": entity_id, "churchId": church_id, "deleted": {"$ne": True}}
            )
            if not source:
                raise ValueError("Sync the selected item before publishing it globally.")

            now = datetime.now(timezone.utc)
            fields: dict[str, Any] = {
                "sourceChurchId": church_id,
                "sourceChurchName": source_church_name,
                "sourceEntityId": entity_id,
                "publishedByUserId": actor_user_id,
                "updatedAt": now,
            }
            if entity_type == "songbook":
                fields["name"] = source.get("name", "Untitled")
            else:
                fields.update(
                    {
                        "title": source.get("title", "Untitled"),
                        "lyrics": source.get("lyrics", ""),
                        "author": source.get("author", ""),
                        "copyright": source.get("copyright", ""),
                        "ccliNumber": source.get("ccliNumber", ""),
                        "segments": source.get("segments", []),
                        "globalSongbookId": self._global_songbook_id(
                            database, church_id, source.get("songbookId")
                        ),
                    }
                )

            published = database[global_collection_name].find_one_and_update(
                {"sourceChurchId": church_id, "sourceEntityId": entity_id},
                {
                    "$set": fields,
                    "$setOnInsert": {"createdAt": now},
                },
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
            if entity_type == "songbook":
                global_songbook_id = str(published["_id"])
                for song in database.songs.find(
                    {
                        "churchId": church_id,
                        "songbookId": entity_id,
                        "deleted": {"$ne": True},
                    }
                ):
                    database.globalSongs.find_one_and_update(
                        {
                            "sourceChurchId": church_id,
                            "sourceEntityId": str(song["_id"]),
                        },
                        {
                            "$set": {
                                "sourceChurchId": church_id,
                                "sourceChurchName": source_church_name,
                                "sourceEntityId": str(song["_id"]),
                                "publishedByUserId": actor_user_id,
                                "globalSongbookId": global_songbook_id,
                                "title": song.get("title", "Untitled"),
                                "lyrics": song.get("lyrics", ""),
                                "author": song.get("author", ""),
                                "copyright": song.get("copyright", ""),
                                "ccliNumber": song.get("ccliNumber", ""),
                                "segments": song.get("segments", []),
                                "updatedAt": now,
                            },
                            "$setOnInsert": {"createdAt": now},
                        },
                        upsert=True,
                        return_document=ReturnDocument.AFTER,
                    )
            self._bump_global_revision(database)
            return self._serialize_record(published)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _bump_sync_revision(database: Any, church_id: str) -> None:
        database.churchRevisions.update_one(
            {"_id": church_id},
            {
                "$inc": {"revision": 1},
                "$set": {"updatedAt": datetime.now(timezone.utc)},
            },
            upsert=True,
        )

    @staticmethod
    def _bump_global_revision(database: Any) -> None:
        database.churchRevisions.update_one(
            {"_id": "__global_catalog__"},
            {
                "$inc": {"revision": 1},
                "$set": {"updatedAt": datetime.now(timezone.utc)},
            },
            upsert=True,
        )

    @staticmethod
    def _combined_sync_revision(database: Any, church_id: str) -> int:
        # One small indexed read retrieves both the selected church marker and
        # the global-catalog marker. A full snapshot is fetched only when this
        # combined value changes.
        revisions = {
            str(document["_id"]): int(document.get("revision", 0))
            for document in database.churchRevisions.find(
                {"_id": {"$in": [church_id, "__global_catalog__"]}},
                {"revision": 1},
            )
        }
        church_revision = revisions.get(church_id, 0)
        global_revision = revisions.get("__global_catalog__", 0)
        return (church_revision << 32) | (global_revision & 0xFFFFFFFF)

    @classmethod
    def _add_source_church_names(
        cls, database: Any, documents: list[dict[str, Any]]
    ) -> None:
        names: dict[str, str] = {}
        for document in documents:
            source_id = str(document.get("sourceChurchId", "")).strip()
            if not source_id:
                document["sourceChurchName"] = "Unknown church"
                continue
            stored_name = str(document.get("sourceChurchName", "")).strip()
            if stored_name:
                names[source_id] = stored_name
                continue
            if source_id not in names:
                church = cls._find_church(database, source_id)
                names[source_id] = (
                    str(church.get("name", "")).strip() if church else ""
                ) or "Unknown church"
            document["sourceChurchName"] = names[source_id]

    def _migrate_legacy_membership(self, database: Any, user: dict[str, Any]) -> None:
        legacy_church_id = user.get("churchId")
        if not legacy_church_id:
            return
        church_id = str(legacy_church_id)
        user_id = str(user["_id"])
        now = datetime.now(timezone.utc)
        database.memberships.update_one(
            {"_id": self._membership_id(church_id, user_id)},
            {
                "$setOnInsert": {
                    "churchId": church_id,
                    "userId": user_id,
                    "role": user.get("role", "member"),
                    "status": "active",
                    "createdAt": now,
                    "updatedAt": now,
                }
            },
            upsert=True,
        )

    @staticmethod
    def _membership_id(church_id: str, user_id: str) -> str:
        return f"{church_id}:{user_id}"

    @classmethod
    def _membership_filter(
        cls, user_id: str, church_id: str
    ) -> dict[str, Any]:
        return {
            "churchId": church_id,
            "userId": user_id,
            "$or": [
                {"status": "active"},
                {"status": {"$exists": False}},
            ],
        }

    def _require_membership(
        self, database: Any, user_id: str, church_id: str
    ) -> dict[str, Any]:
        membership = database.memberships.find_one(
            self._membership_filter(user_id, church_id)
        )
        if not membership:
            user = self._find_user(database, user_id)
            if user and str(user.get("churchId", "")) == church_id:
                self._migrate_legacy_membership(database, user)
                membership = database.memberships.find_one(
                    self._membership_filter(user_id, church_id)
                )
        if not membership:
            raise MembershipRequired(
                "You are not an active member of this church."
            )
        return membership

    def _require_admin(
        self, database: Any, user_id: str, church_id: str
    ) -> dict[str, Any]:
        membership = self._require_membership(database, user_id, church_id)
        if membership.get("role") != "admin":
            raise AdminRequired("Only a church admin can perform this action.")
        return membership

    def _require_account(self, database: Any, user_id: str) -> dict[str, Any]:
        user = self._find_user(database, user_id)
        if not user:
            raise AccountNotRegistered("This Stage Cue account was not found.")
        return user

    @staticmethod
    def _admin_count(database: Any, church_id: str) -> int:
        return database.memberships.count_documents(
            {
                "churchId": church_id,
                "role": "admin",
                "$or": [
                    {"status": "active"},
                    {"status": {"$exists": False}},
                ],
            }
        )

    @staticmethod
    def _find_user(database: Any, user_id: str) -> dict[str, Any] | None:
        filters: list[dict[str, Any]] = [{"_id": user_id}]
        if ObjectId.is_valid(user_id):
            filters.append({"_id": ObjectId(user_id)})
        return database.users.find_one({"$or": filters})

    @staticmethod
    def _find_church(database: Any, church_id: str) -> dict[str, Any] | None:
        filters: list[dict[str, Any]] = [{"_id": church_id}]
        if ObjectId.is_valid(church_id):
            filters.append({"_id": ObjectId(church_id)})
        return database.churches.find_one({"$or": filters})

    @staticmethod
    def _serialize_account(user: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(user["_id"]),
            "googleId": user.get("googleId", ""),
            "email": user.get("email", ""),
            "name": user.get("displayName")
            or user.get("username")
            or user.get("name")
            or user.get("email", "User"),
        }

    @staticmethod
    def _serialize_church(church: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": str(church["_id"]),
            "name": church.get("name", "Church"),
            "location": church.get("location", ""),
        }

    @classmethod
    def _session_from_documents(
        cls,
        user: dict[str, Any],
        church: dict[str, Any],
        membership: dict[str, Any],
    ) -> dict[str, Any]:
        return {
            "user": {
                **cls._serialize_account(user),
                "role": membership.get("role", "member"),
            },
            "church": cls._serialize_church(church),
        }

    @staticmethod
    def _global_songbook_id(
        database: Any, church_id: str, source_songbook_id: str | None
    ) -> str | None:
        if not source_songbook_id:
            return None
        document = database.globalSongbooks.find_one(
            {
                "sourceChurchId": church_id,
                "sourceEntityId": source_songbook_id,
            }
        )
        return str(document["_id"]) if document else None

    @staticmethod
    def _parse_datetime(value: Any) -> datetime:
        if isinstance(value, datetime):
            return value
        if isinstance(value, str):
            try:
                return datetime.fromisoformat(value.replace("Z", "+00:00"))
            except ValueError:
                pass
        return datetime.now(timezone.utc)

    @staticmethod
    def _format_datetime(value: Any) -> str:
        if isinstance(value, datetime):
            return value.astimezone(timezone.utc).isoformat()
        return str(value or "")

    @staticmethod
    def _serialize_record(document: dict[str, Any]) -> dict[str, Any]:
        serialized = {
            key: value
            for key, value in document.items()
            if key not in {"_id", "churchId", "lastOperationId"}
        }
        serialized["id"] = str(document["_id"])
        for key in ("updatedAt", "createdAt", "requestedAt", "reviewedAt"):
            value = serialized.get(key)
            if isinstance(value, datetime):
                serialized[key] = value.astimezone(timezone.utc).isoformat()
        return serialized

    @staticmethod
    def _regex_escape(value: str) -> str:
        special = r"\.^$*+?{}[]|()"
        return "".join(f"\\{character}" if character in special else character for character in value)
