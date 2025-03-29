import asyncio
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Union, Literal


import aiofiles
from aiogram import Bot
from aiogram.types import FSInputFile, InputMediaAudio, InputMediaPhoto
from aiohttp import ClientSession
from mutagen.id3 import ID3NoHeaderError
from yandex_music import ClientAsync, Track, DownloadInfo
from yandex_music.exceptions import NotFoundError
from mutagen.easyid3 import EasyID3
from mutagen.mp3 import MP3


from config_reader import config
YANDEX_TOKEN = config.access_token.get_secret_value()
ADMIN_ID = ''

# Configuration Constants
CODEC = "mp3"
DEFAULT_BITRATE = 320
YTRACK_URL_PATTERN = re.compile(r"https://music\.yandex\.(?:ru|com|kz)/album/\d+/track/\d+")
YALBUM_URL_PATTERN = re.compile(r"https://music\.yandex\.(?:ru|com|kz)/album/\d+")
CHART_COUNTRIES = Literal[
    'world', 'kazakhstan', 'russia', 'armenia', 'georgia', 'azerbaijan',
    'kyrgyzstan', 'moldova', 'tajikistan', 'turkmenistan', 'uzbekistan'
]

# Data Models
@dataclass
class TrackData:
    """Represents metadata for a single track."""
    id: str
    title: str
    artists: List[str]
    duration: float  # In seconds
    album_id: Optional[str] = None
    album_title: Optional[str] = None
    genre: Optional[str] = None
    year: Optional[int] = None
    cover_url: Optional[str] = None
    lyrics: Optional[str] = None
    chart_position: Optional[int] = None
    chart_progress: Optional[str] = None
    chart_shift: Optional[int] = None
    file_path: Optional[str] = None


@dataclass
class AlbumData:
    id: int
    title: str
    track_count: int            # Required field, moved up
    tracks: List[TrackData]     # Assuming this is also required
    genre: Optional[str] = None # Optional fields with defaults come last
    year: Optional[int] = None
    cover_url: Optional[str] = None

# Main SDK Class
class YandexMusicSDK:
    """A senior-level SDK for interacting with the Yandex Music API."""

    def __init__(self, token: str, upload_dir: Optional[str] = None):
        """
        Initialize the SDK with a required token and optional upload directory.

        Args:
            token (str): Yandex Music API token.
            upload_dir (Optional[str]): Directory for downloaded files. Defaults to current working directory.
        """
        self.client = ClientAsync(token=token)
        self.upload_dir = Path(upload_dir) if upload_dir else Path.cwd()
        self.upload_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

    async def __aenter__(self) -> 'YandexMusicSDK':
        """Initialize the client asynchronously."""
        await self.client.init()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Clean up resources on exit."""
        pass  # ClientAsync handles its own cleanup

    # Public Methods
    async def search_tracks(
        self, query: str, count: int = 10, download: bool = False, lyrics: bool = False
    ) -> List[TrackData]:
        """
        Search for tracks with optional downloading and lyrics retrieval.

        Args:
            query (str): Search query.
            count (int): Number of tracks to return. Defaults to 10.
            download (bool): Whether to download tracks. Defaults to False.
            lyrics (bool): Whether to fetch lyrics. Defaults to False.

        Returns:
            List[TrackData]: List of track metadata.
        """
        search_result = await self.client.search(query, type_='track')
        if not search_result or not search_result.tracks:
            self.logger.warning(f"No tracks found for query: {query}")
            return []

        tracks = search_result.tracks.results[:count]
        tasks = [self._process_track(track, download, lyrics) for track in tracks]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        return [r for r in results if r and not isinstance(r, Exception)]

    async def get_track(
        self, track_id: Union[str, int], download: bool = False, lyrics: bool = False
    ) -> Optional[TrackData]:
        """
        Retrieve metadata for a specific track by ID or URL.

        Args:
            track_id (Union[str, int]): Track ID or URL.
            download (bool): Whether to download the track. Defaults to False.
            lyrics (bool): Whether to fetch lyrics. Defaults to False.

        Returns:
            Optional[TrackData]: Track metadata or None if not found.
        """
        track_id = await self._extract_track_id(track_id)
        if not track_id:
            self.logger.error("Invalid track ID or URL")
            return None

        tracks = await self.client.tracks([track_id])
        if not tracks:
            self.logger.error(f"Track {track_id} not found")
            return None
        return await self._process_track(tracks[0], download, lyrics)

    async def get_currently_playing(self, device: str, lyrics: bool = False) -> Optional[TrackData]:
        """
        Get the currently playing track for a specified device.

        Args:
            device (str): Device identifier.
            lyrics (bool): Whether to fetch lyrics. Defaults to False.

        Returns:
            Optional[TrackData]: Current track metadata or None if not available.
        """
        queues = await self.client.queues_list(device)
        if not queues:
            self.logger.error(f"No queue found for device: {device}")
            return None

        last_queue = await self.client.queue(queues[0].id)
        track_id = last_queue.get_current_track()
        if not track_id:
            return None
        track = await track_id.fetch_track_async()
        return await self._process_track(track, download=False, lyrics=lyrics)

    async def get_album(self, album_id: Union[str, int]) -> Optional[AlbumData]:
        """
        Retrieve metadata for an album by ID or URL.

        Args:
            album_id (Union[str, int]): Album ID or URL.

        Returns:
            Optional[AlbumData]: Album metadata or None if not found.
        """
        album_id = self._extract_album_id(album_id) if isinstance(album_id, str) else album_id
        if not album_id:
            self.logger.error("Invalid album ID or URL")
            return None

        album = await self.client.albums_with_tracks(album_id)
        if not album:
            self.logger.error(f"Album {album_id} not found")
            return None

        tracks = [
            await self._process_track(track, download=False, lyrics=False)
            for volume in album.volumes
            for track in volume
        ]
        return AlbumData(
            id=album.id,
            title=album.title,
            genre=album.genre,
            year=album.year,
            track_count=album.track_count,
            tracks=[t for t in tracks if t],
            cover_url=album.get_cover_url('1000x1000') if album.cover_uri else None
        )

    async def get_chart(
        self, country: CHART_COUNTRIES, count: int = 10, download: bool = False
    ) -> Optional[List[TrackData]]:
        """
        Retrieve chart data for a specified country.

        Args:
            country (CHART_COUNTRIES): Country code from CHART_COUNTRIES.
            count (int): Number of tracks to return. Defaults to 10.
            download (bool): Whether to download tracks. Defaults to False.

        Returns:
            Optional[List[TrackData]]: List of chart track metadata or None if not available.
        """
        chart = await self.client.chart(country)
        if not chart or not chart.chart.tracks:
            self.logger.error(f"No chart data for {country}")
            return None

        tracks = []
        for track_short in chart.chart.tracks[:count]:
            track = track_short.track
            chart_info = track_short.chart
            track_data = await self._process_track(track, download, lyrics=False)
            if track_data:
                track_data.chart_position = chart_info.position
                track_data.chart_progress = chart_info.progress
                track_data.chart_shift = chart_info.shift
                tracks.append(track_data)
        return tracks

    # Private Helper Methods
    async def _process_track(self, track: Track, download: bool, lyrics: bool) -> Optional[TrackData]:
        """Process a track to extract metadata, optionally download, and fetch lyrics."""
        try:
            album = track.albums[0] if track.albums else None
            download_info = await track.get_download_info_async(get_direct_links=True)
            lyrics_text = await self._get_lyrics(track) if lyrics else None

            track_data = TrackData(
                id=str(track.id),
                title=track.title,
                artists=[a.name for a in track.artists],
                duration=track.duration_ms / 1000.0,
                album_id=str(album.id) if album else None,
                album_title=album.title if album else None,
                genre=album.genre if album else None,
                year=album.year if album else None,
                cover_url=track.get_cover_url('1000x1000') if track.cover_uri else None,
                lyrics=lyrics_text

            )

            if download:
                filename = self._generate_filename(track)
                file_path = await self._download_track(download_info, filename)
                if file_path:
                    self._insert_metadata(track_data, file_path)
                    track_data.file_path = file_path

            return track_data
        except Exception as e:
            self.logger.error(f"Failed to process track {track.id}: {e}")
            return None

    async def _get_lyrics(self, track: Track) -> Optional[str]:
        """Fetch lyrics for a track."""
        try:
            lyrics_data = await track.get_lyrics_async('TEXT')
            return await lyrics_data.fetch_lyrics_async()
        except NotFoundError:
            self.logger.debug(f"No lyrics found for track {track.id}")
            return None
        except Exception as e:
            self.logger.error(f"Error fetching lyrics for track {track.id}: {e}")
            return None

    def _generate_filename(self, track: Track) -> str:
        """Generate a sanitized, descriptive filename for a track."""
        artist = "_".join(a.name for a in track.artists).replace("/", "_").replace(" ", "_")
        title = track.title.replace("/", "_").replace(" ", "_")

        safe_name = re.sub(r'[\\/:*?"<>|]', '_', f"{artist}-{title}")
        return f"{safe_name}.{CODEC}"

    async def _download_track(self, download_info: List[DownloadInfo], filename: str) -> Optional[str]:
        """Download a track using the best available quality."""
        mp3_infos = [info for info in download_info if info.codec == 'mp3']
        if not mp3_infos:
            self.logger.error("No MP3 download available")
            return None
        best_info = max(mp3_infos, key=lambda x: x.bitrate_in_kbps or DEFAULT_BITRATE)
        download_url = best_info.direct_link
        file_path = self.upload_dir / filename

        if file_path.exists():
            self.logger.info(f"Track {filename} already exists at {file_path}")
            return str(file_path)

        async with ClientSession() as session:
            async with session.get(download_url) as response:
                if response.status == 200:
                    async with aiofiles.open(file_path, 'wb') as f:
                        await f.write(await response.read())
                    self.logger.info(f"Downloaded track to {file_path}")
                    return str(file_path)
                self.logger.error(f"Download failed for {filename}: HTTP {response.status}")
                return None

    def _insert_metadata(self, track_data: TrackData, file_path: str) -> None:
        """Insert metadata into a downloaded MP3 file."""
        try:
            try:
                audio = EasyID3(file_path)
            except ID3NoHeaderError:
                audio = MP3(file_path)
                audio.add_tags()
                audio.save()
                audio = EasyID3(file_path)

            audio['title'] = track_data.title
            audio['artist'] = ", ".join(track_data.artists)
            if track_data.album_title:
                audio['album'] = track_data.album_title
            if track_data.year:
                audio['date'] = str(track_data.year)
            if track_data.genre:
                audio['genre'] = track_data.genre
            if track_data.duration:
                audio['length'] = str(int(track_data.duration * 1000))
            audio.save()
            self.logger.debug(f"Metadata inserted for {file_path}")
        except Exception as e:
            self.logger.error(f"Failed to insert metadata for {file_path}: {e}")

    @staticmethod
    async def _extract_track_id(track: Union[str, int]) -> Optional[str]:
        """Extract track ID from a URL or direct ID."""
        if YTRACK_URL_PATTERN.match(track):
            # Extract the track ID from the URL (last segment after '/')
            return track.split('/')[-1]
        elif track.isdigit():
            return track  # Return as-is if it's a numeric ID
        return None

    @staticmethod
    def _extract_album_id(album: str) -> Optional[int]:
        """Extract album ID from a URL."""
        if album.isdigit():
            return int(album)  # Directly return numeric ID
        match = YALBUM_URL_PATTERN.match(album)
        if match:
            # Extract the album ID from the URL (last segment after '/')
            return int(album.split('/')[-1])
        return None  # Return None if neither a URL nor a numeric ID

class YANDEX_MUSIC_TRACK_CAPTION:
    def __init__(self, track):
        self.track = track

    def format(self) -> str:
        return (
            f"<b>🎵 Track:</b> <a href='https://music.yandex.com/album/{self.track.album_id}/track/{self.track.id}'>"
            f"{self.track.title}</a> • {self.track.year}\n"
            f"<b>👥 Artists:</b> <i>{', '.join(a for a in self.track.artists)}</i>\n"
            f"<b>📀 Album:</b> <a href='https://music.yandex.com/album/{self.track.album_id}'>"
            f"{self.track.album_title}</a>\n"
            f"<b>🎶 Genre:</b> <i>{self.track.genre.capitalize()}</i>\n"
            f"<b>⏱️ Duration:</b> <code>{int(self.track.duration // 60)}:{int(self.track.duration % 60):02d}</code>"
        )


async def download_and_replace_yandex(track, inline_message_id, bot: Bot):
    """Download a Yandex Music track and replace the inline message with the audio."""
    file_path = None
    try:
        async with YandexMusicSDK(token=YANDEX_TOKEN, upload_dir="./downloads") as ym:
            # Download the track using its ID
            track = await ym.get_track(track.id, download=True)
            if not track:
                raise FileNotFoundError("Failed to download track")

            file_path = track.file_path
            audio_input = FSInputFile(file_path)

            # Upload the audio to a media chat to get a file_id
            # Replace MEDIA_CHAT_ID with your bot's chat ID or a dedicated channel ID
            msg = await bot.send_audio(
                chat_id=ADMIN_ID,  # Define this constant or use bot.get_me() ID
                audio=audio_input,
                title=track.title,
                performer=", ".join(track.artists)
            )
            file_id = msg.audio.file_id

            # Edit the inline message with the actual audio
            await bot.edit_message_media(
                inline_message_id=inline_message_id,
                media=InputMediaAudio(
                    media=file_id,
                    caption=f"{', '.join(track.artists)} - {track.title}\n🔗 <a href='https://music.yandex.com/album/{track.album_id}/track/{track.id}'>Yandex Music</a>",
                    parse_mode="HTML"
                )
            )
    except Exception as e:
        #logger.error(f"Error downloading Yandex Music track: {e}")
        # On error, replace with an error message and cover image
        await bot.edit_message_media(
            inline_message_id=inline_message_id,
            media=InputMediaPhoto(
                media=track.cover_url,
                caption="❌ Error downloading track"
            )
        )
    finally:
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                #logger.error(f"Failed to remove temporary file {file_path}: {e}")
                print(f"Failed to remove temporary file {file_path}: {e}")
# #Example Usage
# async def main():
#     # Configure logging
#     logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")

#     # Use the SDK
#     async with YandexMusicSDK(token=config.access_token.get_secret_value(), upload_dir="./downloads") as sdk:
#         # Search and download a track

#         tracks = await sdk.search_tracks("je te laisserai des mots", count=3, download=True, lyrics=True)

#         for track in tracks:

#             print(f"Found: {track.title} by {', '.join(track.artists)}")

#         # # Get a specific track
#         # track = await sdk.get_track("https://music.yandex.ru/album/123/track/456", download=False)
#         # if track:
#         #     print(f"Got track: {track.title}")

#         # # Get an album
#         # album = await sdk.get_album("123")
#         # if album:
#         #     print(f"Album: {album.title} with {album.track_count} tracks")

#         # Get chart
#         chart_tracks = await sdk.get_chart("kazakhstan", count=2)
#         if chart_tracks:
#             for t in chart_tracks:
#                 print(f"Chart #{t.chart_position}: {t.title}")

# if __name__ == "__main__":
#     asyncio.run(main())