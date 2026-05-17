import json

from fastapi.testclient import TestClient

import src.server as server
from src.services.data_store import DataStore
from src.services.music_library_service import MusicLibraryService
from src.services.photo_library_service import PhotoLibraryService


class FakeCaptioner:
    model_name = "fake-qwen-vision"

    def caption(self, image_url, metadata=None):
        return {
            "description": "A family picnic in the park",
            "family_labels": ["granddaughter"],
            "scene": "park",
            "objects": ["blanket"],
            "activity": "picnic",
            "searchable_text": "family picnic park granddaughter",
        }


class FakeOrchestrator:
    def __init__(self, root_dir):
        self.data_store = DataStore(root_dir)
        self.photo_library_service = PhotoLibraryService(self.data_store, captioner=FakeCaptioner())
        self.music_library_service = MusicLibraryService(self.data_store)


def _client(monkeypatch, tmp_path):
    fake = FakeOrchestrator(tmp_path)
    monkeypatch.setattr(server, "orchestrator", fake)
    return TestClient(server.app), fake


def test_photo_library_sync_list_caption_and_import_api(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)

    sync = client.post(
        "/api/photo_library/sync",
        json={
            "elder_user_id": "elder_001",
            "photos": [
                {
                    "photo_id": "p1",
                    "url": "http://example.test/p1.jpg",
                    "original_file_name": "p1.jpg",
                    "frontend_caption": "Granddaughter birthday",
                    "tags": ["birthday"],
                }
            ],
        },
    )
    listed = client.get(
        "/api/photo_library/photos",
        params={"elder_user_id": "elder_001", "query": "birthday"},
    )
    captioned = client.post(
        "/api/photo_library/caption_pending",
        params={"elder_user_id": "elder_001", "limit": 1, "force": True},
        json={"photo_ids": ["p1"]},
    )
    imported = client.post(
        "/api/photo_library/import",
        params={"elder_user_id": "elder_001", "file_name": "album.json"},
        content=json.dumps(
            {
                "photos": [
                    {
                        "photo_id": "p2",
                        "url": "http://example.test/p2.jpg",
                        "description": "Family beach walk",
                    }
                ]
            }
        ).encode("utf-8"),
    )

    assert sync.status_code == 200
    assert sync.json()["data"]["upserted"] == 1
    assert listed.status_code == 200
    assert listed.json()["data"][0]["photo_id"] == "p1"
    assert captioned.status_code == 200
    assert captioned.json()["data"]["captioned"] == 1
    assert imported.status_code == 200
    assert imported.json()["data"]["upserted"] == 1


def test_music_library_sync_list_and_match_api(monkeypatch, tmp_path):
    client, _ = _client(monkeypatch, tmp_path)

    sync = client.post(
        "/api/music/library",
        json={
            "elder_user_id": "elder_001",
            "songs": [
                {
                    "music_id": "song_001",
                    "name": "Morning Sunshine",
                    "description": "Bright oldies for waking up",
                    "mood_tags": ["bright"],
                    "scene_tags": ["morning"],
                    "playable_ref": "local://morning-sunshine",
                }
            ],
        },
    )
    listed = client.get("/api/music/library", params={"elder_user_id": "elder_001"})
    matched = client.get(
        "/api/music/library/match",
        params={"elder_user_id": "elder_001", "query": "bright morning song"},
    )

    assert sync.status_code == 200
    assert sync.json()["data"]["upserted"] == 1
    assert listed.status_code == 200
    assert listed.json()["data"][0]["music_id"] == "song_001"
    assert matched.status_code == 200
    assert matched.json()["data"]["song"]["playable_ref"] == "local://morning-sunshine"
