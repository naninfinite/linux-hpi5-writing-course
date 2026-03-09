from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory
import time
import unittest

from gbtw.db import Database
from gbtw.profiles import (
    DEFAULT_PROFILE_NAME,
    ProfileStore,
    ProfileValidationError,
)


class ProfileStoreTests(unittest.TestCase):
    def test_bootstraps_default_profile_with_legacy_db_path(self) -> None:
        with TemporaryDirectory() as tmp:
            legacy_db = Path(tmp) / "progress.db"
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=legacy_db,
                profiles_dir=Path(tmp) / "profiles",
            )

            profiles = store.list_profiles()

            self.assertEqual(len(profiles), 1)
            self.assertEqual(profiles[0].profile_id, "default")
            self.assertEqual(profiles[0].display_name, DEFAULT_PROFILE_NAME)
            self.assertEqual(profiles[0].db_path, legacy_db)

    def test_create_profile_generates_unique_slug_ids(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "progress.db",
                profiles_dir=Path(tmp) / "profiles",
            )

            first = store.create_profile("Alice Smith")
            second = store.create_profile("Alice-Smith")

            self.assertEqual(first.profile_id, "alice-smith")
            self.assertEqual(second.profile_id, "alice-smith-2")
            self.assertEqual(second.db_path, Path(tmp) / "profiles" / "alice-smith-2" / "progress.db")

    def test_create_profile_rejects_blank_and_duplicate_names(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "progress.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            store.create_profile("Alice")

            with self.assertRaises(ProfileValidationError):
                store.create_profile("   ")
            with self.assertRaises(ProfileValidationError):
                store.create_profile("alice")

    def test_mark_used_updates_last_used_at_and_sort_order(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "progress.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            first = store.create_profile("Alice")
            second = store.create_profile("Bob")

            time.sleep(0.01)
            refreshed = store.mark_used(first.profile_id)
            profiles = store.list_profiles()

            self.assertGreater(refreshed.last_used_at, second.last_used_at)
            self.assertEqual(profiles[0].profile_id, first.profile_id)

    def test_rename_profile_updates_display_name_only(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "progress.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            created = store.create_profile("Alice")

            renamed = store.rename_profile(created.profile_id, "Family Writer")

            self.assertEqual(renamed.profile_id, created.profile_id)
            self.assertEqual(renamed.db_path, created.db_path)
            self.assertEqual(store.get_profile(created.profile_id).display_name, "Family Writer")

    def test_rename_profile_rejects_duplicate_name(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "progress.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            alice = store.create_profile("Alice")
            store.create_profile("Bob")

            with self.assertRaises(ProfileValidationError):
                store.rename_profile(alice.profile_id, "Bob")

    def test_profile_databases_keep_progress_isolated(self) -> None:
        with TemporaryDirectory() as tmp:
            store = ProfileStore(
                Path(tmp) / "profiles.json",
                legacy_db_path=Path(tmp) / "progress.db",
                profiles_dir=Path(tmp) / "profiles",
            )
            alice = store.create_profile("Alice")
            bob = store.create_profile("Bob")

            alice_db = Database(alice.db_path)
            bob_db = Database(bob.db_path)
            alice_db.set_preference("last_mode", "freewrite")
            alice_db.create_entry("part1/a.md", "freewrite", "alice draft")

            self.assertEqual(alice_db.get_preference("last_mode"), "freewrite")
            self.assertIsNone(bob_db.get_preference("last_mode"))
            self.assertEqual(alice_db.get_latest_entry("part1/a.md", "freewrite").content, "alice draft")
            self.assertIsNone(bob_db.get_latest_entry("part1/a.md", "freewrite"))

            alice_db.close()
            bob_db.close()
