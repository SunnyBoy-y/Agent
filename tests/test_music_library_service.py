from src.schemas.music_library import MusicLibrarySong, MusicLibrarySyncRequest
from src.services.data_store import DataStore
from src.services.music_library_service import MusicLibraryService


def test_music_library_sync_list_and_match_by_description(tmp_path):
    service = MusicLibraryService(DataStore(tmp_path))

    result = service.sync_library(
        MusicLibrarySyncRequest(
            elder_user_id="elder_001",
            songs=[
                MusicLibrarySong(
                    music_id="song_calm",
                    name="Evening Calm",
                    artist="Care Radio",
                    description="Soft relaxing piano for anxiety relief before sleep",
                    aliases=["calm piano"],
                    mood_tags=["calm", "relaxing"],
                    scene_tags=["sleep", "comfort"],
                    playable_ref="local://evening-calm",
                )
            ],
        )
    )
    match = service.match_song("elder_001", "I feel anxious and want relaxing piano", limit=1)

    assert result["upserted"] == 1
    assert match["matched"] is True
    assert match["song"]["music_id"] == "song_calm"
    assert match["song"]["playable_ref"] == "local://evening-calm"


def test_music_library_replace_removes_old_songs(tmp_path):
    service = MusicLibraryService(DataStore(tmp_path))
    service.sync_library(
        MusicLibrarySyncRequest(
            elder_user_id="elder_001",
            songs=[MusicLibrarySong(music_id="old", name="Old Song")],
        )
    )

    service.sync_library(
        MusicLibrarySyncRequest(
            elder_user_id="elder_001",
            sync_mode="replace",
            songs=[MusicLibrarySong(music_id="new", name="New Song")],
        )
    )

    songs = service.list_records("elder_001")
    assert [song.music_id for song in songs] == ["new"]
