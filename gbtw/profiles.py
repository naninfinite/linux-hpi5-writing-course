from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import APP_DATA_DIR, DB_PATH

PROFILES_DIR = APP_DATA_DIR / "profiles"
PROFILE_REGISTRY_PATH = APP_DATA_DIR / "profiles.json"
MAX_PROFILE_NAME_LENGTH = 40
DEFAULT_PROFILE_ID = "default"
DEFAULT_PROFILE_NAME = "Default"


class ProfileStoreError(Exception):
    pass


class ProfileRegistryError(ProfileStoreError):
    pass


class ProfileValidationError(ProfileStoreError):
    pass


@dataclass(slots=True, frozen=True)
class ProfileRecord:
    profile_id: str
    display_name: str
    db_path: Path
    created_at: datetime
    last_used_at: datetime


class ProfileStore:
    def __init__(
        self,
        registry_path: Path = PROFILE_REGISTRY_PATH,
        *,
        legacy_db_path: Path = DB_PATH,
        profiles_dir: Path = PROFILES_DIR,
    ) -> None:
        self.registry_path = registry_path
        self.legacy_db_path = legacy_db_path
        self.profiles_dir = profiles_dir

    def list_profiles(self) -> list[ProfileRecord]:
        records = self._load_profiles()
        return sorted(
            records,
            key=lambda record: (
                -record.last_used_at.timestamp(),
                record.display_name.lower(),
            ),
        )

    def create_profile(self, display_name: str) -> ProfileRecord:
        name = display_name.strip()
        if not name:
            raise ProfileValidationError("Enter a profile name.")
        if len(name) > MAX_PROFILE_NAME_LENGTH:
            raise ProfileValidationError(f"Profile names must be {MAX_PROFILE_NAME_LENGTH} characters or fewer.")
        profiles = self._load_profiles()
        if any(record.display_name.casefold() == name.casefold() for record in profiles):
            raise ProfileValidationError("That profile name already exists.")
        profile_id = self._unique_profile_id(_slugify(name), {record.profile_id for record in profiles})
        timestamp = datetime.now().astimezone()
        record = ProfileRecord(
            profile_id=profile_id,
            display_name=name,
            db_path=self.profiles_dir / profile_id / "progress.db",
            created_at=timestamp,
            last_used_at=timestamp,
        )
        profiles.append(record)
        self._save_profiles(profiles)
        return record

    def mark_used(self, profile_id: str) -> ProfileRecord:
        profiles = self._load_profiles()
        updated_at = datetime.now().astimezone()
        updated_record: ProfileRecord | None = None
        refreshed: list[ProfileRecord] = []
        for record in profiles:
            if record.profile_id == profile_id:
                updated_record = ProfileRecord(
                    profile_id=record.profile_id,
                    display_name=record.display_name,
                    db_path=record.db_path,
                    created_at=record.created_at,
                    last_used_at=updated_at,
                )
                refreshed.append(updated_record)
            else:
                refreshed.append(record)
        if updated_record is None:
            raise KeyError(f"profile {profile_id} not found")
        self._save_profiles(refreshed)
        return updated_record

    def get_profile(self, profile_id: str) -> ProfileRecord | None:
        return next((record for record in self._load_profiles() if record.profile_id == profile_id), None)

    def _load_profiles(self) -> list[ProfileRecord]:
        data = self._read_registry()
        if data is None:
            default = self._default_profile()
            self._save_profiles([default])
            return [default]
        raw_profiles = data.get("profiles")
        if not isinstance(raw_profiles, list):
            raise ProfileRegistryError(f"Invalid profile registry: {self.registry_path}")
        profiles: list[ProfileRecord] = []
        for item in raw_profiles:
            profiles.append(self._profile_from_json(item))
        if not profiles:
            profiles = [self._default_profile()]
            self._save_profiles(profiles)
        return profiles

    def _read_registry(self) -> dict[str, Any] | None:
        if not self.registry_path.exists():
            return None
        try:
            raw_data = json.loads(self.registry_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ProfileRegistryError(f"Unable to read profile registry {self.registry_path}: {exc}") from exc
        if not isinstance(raw_data, dict):
            raise ProfileRegistryError(f"Invalid profile registry: {self.registry_path}")
        return raw_data

    def _save_profiles(self, profiles: list[ProfileRecord]) -> None:
        self.registry_path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "profiles": [
                {
                    "profile_id": record.profile_id,
                    "display_name": record.display_name,
                    "db_path": str(record.db_path),
                    "created_at": record.created_at.isoformat(),
                    "last_used_at": record.last_used_at.isoformat(),
                }
                for record in profiles
            ]
        }
        self.registry_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    def _default_profile(self) -> ProfileRecord:
        timestamp = datetime.now().astimezone()
        return ProfileRecord(
            profile_id=DEFAULT_PROFILE_ID,
            display_name=DEFAULT_PROFILE_NAME,
            db_path=self.legacy_db_path,
            created_at=timestamp,
            last_used_at=timestamp,
        )

    def _profile_from_json(self, item: Any) -> ProfileRecord:
        if not isinstance(item, dict):
            raise ProfileRegistryError(f"Invalid profile registry: {self.registry_path}")
        try:
            profile_id = str(item["profile_id"])
            display_name = str(item["display_name"])
            db_path = Path(str(item["db_path"]))
            created_at = datetime.fromisoformat(str(item["created_at"]))
            last_used_at = datetime.fromisoformat(str(item["last_used_at"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ProfileRegistryError(f"Invalid profile registry: {self.registry_path}") from exc
        return ProfileRecord(
            profile_id=profile_id,
            display_name=display_name,
            db_path=db_path,
            created_at=created_at,
            last_used_at=last_used_at,
        )

    def _unique_profile_id(self, base_id: str, existing_ids: set[str]) -> str:
        if base_id not in existing_ids:
            return base_id
        suffix = 2
        while f"{base_id}-{suffix}" in existing_ids:
            suffix += 1
        return f"{base_id}-{suffix}"


def _slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    collapsed = re.sub(r"[^a-zA-Z0-9]+", "-", normalized).strip("-").lower()
    return collapsed or "profile"
