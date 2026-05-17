import json
from unittest.mock import patch

from src.schemas.music_library import MusicLibrarySong, MusicLibrarySyncRequest
from src.schemas.photo_library import PhotoLibraryItem, PhotoLibrarySyncRequest
from src.services.data_store import DataStore
from src.services.music_library_service import MusicLibraryService
from src.services.photo_library_service import PhotoLibraryService
from src.tools.professional_skills import ProfessionalSkills


def _invoke_tool(tool_obj, payload):
    if hasattr(tool_obj, "invoke"):
        return tool_obj.invoke(payload)
    return tool_obj(**payload)


def test_search_family_photos_prefers_local_photo_library(tmp_path):
    old_service = ProfessionalSkills.photo_library_service
    service = PhotoLibraryService(DataStore(tmp_path))
    service.sync_photos(
        PhotoLibrarySyncRequest(
            elder_user_id="elder_001",
            photos=[
                PhotoLibraryItem(
                    photo_id="local-photo",
                    url="http://example.test/local.jpg",
                    frontend_caption="Granddaughter at a picnic",
                    tags=["picnic"],
                    people=["granddaughter"],
                )
            ],
        )
    )
    ProfessionalSkills.register_photo_library_service(service)

    try:
        with patch("src.tools.professional_skills.requests.get", side_effect=AssertionError("external search should not run")):
            result = json.loads(
                _invoke_tool(
                    ProfessionalSkills.search_family_photos,
                    {"keyword": "picnic", "username": "elder_001"},
                )
            )
    finally:
        ProfessionalSkills.register_photo_library_service(old_service)

    assert result["status"] == "success"
    assert result["source"] == "local_photo_library"
    assert result["photos"][0]["photo_id"] == "local-photo"


def test_play_music_uses_local_music_library_when_user_id_available(tmp_path):
    old_service = ProfessionalSkills.music_library_service
    service = MusicLibraryService(DataStore(tmp_path))
    service.sync_library(
        MusicLibrarySyncRequest(
            elder_user_id="elder_001",
            songs=[
                MusicLibrarySong(
                    music_id="song_sleep",
                    name="Sleep Piano",
                    description="Quiet piano for sleep",
                    mood_tags=["quiet"],
                    scene_tags=["sleep"],
                    playable_ref="local://sleep-piano",
                )
            ],
        )
    )
    ProfessionalSkills.register_music_library_service(service)

    try:
        result = json.loads(
            _invoke_tool(
                ProfessionalSkills.play_music,
                {"query": "quiet sleep music", "elder_user_id": "elder_001"},
            )
        )
    finally:
        ProfessionalSkills.register_music_library_service(old_service)

    assert result["trigger_music"] is True
    assert result["source"] == "music_library"
    assert result["music_id"] == "song_sleep"
    assert result["playable_ref"] == "local://sleep-piano"
