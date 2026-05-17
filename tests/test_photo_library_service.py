import json
import sqlite3

from src.schemas.photo_library import PhotoLibraryItem, PhotoLibrarySyncRequest
from src.services.data_store import DataStore
from src.services.photo_library_service import PhotoLibraryService


class FakeCaptioner:
    model_name = "fake-qwen-vision"

    def caption(self, image_url, metadata=None):
        return {
            "description": "Granddaughter is smiling during a park picnic.",
            "people_hint": ["granddaughter"],
            "family_labels": ["granddaughter"],
            "scene": "park",
            "objects": ["picnic blanket"],
            "activity": "picnic",
            "emotion_hint": "happy",
            "time_hint": "spring afternoon",
            "searchable_text": "granddaughter park picnic happy",
            "safety_flags": [],
        }


def test_photo_library_sync_and_search_uses_frontend_metadata(tmp_path):
    service = PhotoLibraryService(DataStore(tmp_path))

    result = service.sync_photos(
        PhotoLibrarySyncRequest(
            elder_user_id="elder_001",
            photos=[
                PhotoLibraryItem(
                    photo_id="p1",
                    url="http://example.test/p1.jpg",
                    original_file_name="p1.jpg",
                    frontend_caption="Granddaughter at the park picnic",
                    tags=["family", "picnic"],
                    people=["granddaughter"],
                    location="city park",
                )
            ],
        )
    )
    matches = service.search_for_agent("elder_001", "picnic", limit=5)

    assert result["upserted"] == 1
    assert matches[0]["photo_id"] == "p1"
    assert matches[0]["caption_source"] == "frontend"
    assert matches[0]["metadata_available"] is True


def test_photo_library_imports_json_manifest_and_preserves_raw_file(tmp_path):
    service = PhotoLibraryService(DataStore(tmp_path))
    payload = {
        "photos": [
            {
                "uuid": "file-1",
                "originalFileName": "family.jpg",
                "fileType": "image/jpeg",
                "description": "Family birthday dinner",
                "tags": ["birthday"],
                "people": ["daughter"],
            }
        ]
    }

    result = service.import_library_bytes(
        "elder_001",
        json.dumps(payload).encode("utf-8"),
        file_name="album.json",
    )
    matches = service.search_for_agent("elder_001", "birthday", limit=5)

    assert result["upserted"] == 1
    assert result["stored_path"].endswith("/album.json")
    assert matches[0]["file_uuid"] == "file-1"
    assert matches[0]["desc"] == "Family birthday dinner"


def test_photo_library_imports_sqlite_manifest(tmp_path):
    source = tmp_path / "album.sqlite"
    conn = sqlite3.connect(source)
    conn.execute(
        "CREATE TABLE photos (photo_id TEXT, url TEXT, original_file_name TEXT, "
        "frontend_caption TEXT, tags TEXT, people TEXT, location TEXT)"
    )
    conn.execute(
        "INSERT INTO photos VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            "sqlite-photo",
            "http://example.test/sqlite.jpg",
            "sqlite.jpg",
            "Old family trip",
            "travel, family",
            "son",
            "beach",
        ),
    )
    conn.commit()
    conn.close()

    service = PhotoLibraryService(DataStore(tmp_path / "store"))
    result = service.import_library_bytes(
        "elder_001",
        source.read_bytes(),
        file_name="album.sqlite",
    )
    matches = service.search_for_agent("elder_001", "beach", limit=5)

    assert result["upserted"] == 1
    assert matches[0]["photo_id"] == "sqlite-photo"
    assert matches[0]["location"] == "beach"


def test_photo_library_caption_pending_updates_search_cache(tmp_path):
    service = PhotoLibraryService(DataStore(tmp_path), captioner=FakeCaptioner())
    service.sync_photos(
        PhotoLibrarySyncRequest(
            elder_user_id="elder_001",
            photos=[
                PhotoLibraryItem(
                    photo_id="p2",
                    url="http://example.test/p2.jpg",
                    original_file_name="unknown.jpg",
                )
            ],
        )
    )

    result = service.caption_pending("elder_001", limit=5)
    matches = service.search_for_agent("elder_001", "granddaughter picnic", limit=5)

    assert result.captioned == 1
    assert matches[0]["photo_id"] == "p2"
    assert matches[0]["caption_source"] == "qwen_vision"


def test_photo_library_respects_no_backend_cache_permission(tmp_path):
    service = PhotoLibraryService(DataStore(tmp_path))

    result = service.sync_photos(
        PhotoLibrarySyncRequest(
            elder_user_id="elder_001",
            photos=[
                PhotoLibraryItem(
                    photo_id="private",
                    url="http://example.test/private.jpg",
                    frontend_caption="Private photo",
                    permission={"allow_backend_cache": False, "allow_visual_caption": False},
                )
            ],
        )
    )

    assert result["skipped_permission"] == 1
    assert service.list_records("elder_001") == []
