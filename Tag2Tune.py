import glob
import json
import os
import platform
import queue
import re
import subprocess
import sys
import threading
import tkinter as tk
import webbrowser
from dataclasses import dataclass
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk, simpledialog
import urllib.parse as urlparse
from typing import Dict, List, Optional, Set, Tuple

try:
    import spotipy
    from spotipy.oauth2 import SpotifyOAuth
except ImportError:  # pragma: no cover
    spotipy = None

try:
    from mutagen import File
    from mutagen.easyid3 import EasyID3
except ImportError:  # pragma: no cover
    File = None
    EasyID3 = None

CONFIG_FILE = "config.json"
CACHE_FILE = "spotify_cache.json"
LOG_FOLDER = "logs"
UNMATCHED_FILE = "unmatched_tracks.txt"
OAUTH_CACHE = ".spotify_oauth_cache"
LOG_DATE_FORMAT = "%Y-%m-%d"
TIMESTAMP_FORMAT = "%Y-%m-%d %H:%M:%S"
CACHE_TTL_DAYS = 30

SUPPORTED_EXTENSIONS: Dict[str, List[str]] = {
    "MP3 (*.mp3)": [".mp3"],
    "FLAC Lossless (*.flac)": [".flac"],
    "WAV Hi-Res (*.wav)": [".wav"],
    "M4A/ALAC (*.m4a)": [".m4a"],
    "AAC (*.aac)": [".aac"],
    "OGG (*.ogg)": [".ogg"],
    "AIFF (*.aiff)": [".aiff"],
    "WMA (*.wma)": [".wma"],
    "APE (*.ape)": [".ape"],
    "All Audio Formats": [
        ".mp3",
        ".flac",
        ".wav",
        ".m4a",
        ".aac",
        ".ogg",
        ".wma",
        ".aiff",
        ".ape",
    ],
}

DEFAULT_CONFIG: Dict[str, str] = {
    "SPOTIFY_CLIENT_ID": "",
    "SPOTIFY_CLIENT_SECRET": "",
    "SPOTIFY_REDIRECT_URI": "http://127.0.0.1:8888/callback",
    "DEFAULT_MUSIC_DIRECTORY": "",
}

FILENAME_PATTERNS = [
    re.compile(r"^\s*\d+\s*[-_. ]+\s*(?P<artist>[^-]+?)\s*[-_]\s*(?P<title>.+)$"),
    re.compile(r"^\s*(?P<artist>[^-]+?)\s*-\s*(?P<title>.+)$"),
    re.compile(r"^\s*(?P<artist>[^_]+?)_(?P<title>.+)$"),
    re.compile(r"^\s*(?P<artist>[^\|]+?)\s*\|\s*(?P<title>.+)$"),
]


@dataclass
class ScanStatistics:
    folders_scanned: int = 0
    tracks_found: int = 0
    tagged_tracks: int = 0
    fallback_tracks: int = 0


@dataclass
class SyncStatistics:
    processed_tracks: int = 0
    matches: int = 0
    cache_hits: int = 0
    failed_matches: int = 0
    duplicates_removed: int = 0


class LogManager:
    def __init__(self, folder: str) -> None:
        self.folder = folder
        os.makedirs(self.folder, exist_ok=True)
        self.current_log_path = self._daily_log_path()

    def _daily_log_path(self) -> str:
        filename = f"{datetime.now().strftime(LOG_DATE_FORMAT)}.log"
        return os.path.join(self.folder, filename)

    def write(self, message: str) -> None:
        try:
            path = self._daily_log_path()
            if path != self.current_log_path:
                self.current_log_path = path
            timestamp = datetime.now().strftime(TIMESTAMP_FORMAT)
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(f"[{timestamp}] {message}\n")
        except Exception as error:
            print(f"[!] Failed to write log entry: {error}")


class ConfigManager:
    def __init__(self, path: str = CONFIG_FILE) -> None:
        self.path = path
        self.data = self._load_or_create()

    def _load_or_create(self) -> Dict[str, str]:
        if not os.path.exists(self.path):
            try:
                with open(self.path, "w", encoding="utf-8") as handle:
                    json.dump(DEFAULT_CONFIG, handle, indent=4)
            except Exception as error:
                print(f"[!] Could not create config file: {error}")
            return DEFAULT_CONFIG.copy()

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                return {**DEFAULT_CONFIG, **data}
        except Exception as error:
            print(f"[!] Could not read config file: {error}")
            return DEFAULT_CONFIG.copy()

    def is_valid(self) -> bool:
        return bool(
            self.data.get("SPOTIFY_CLIENT_ID")
            and self.data.get("SPOTIFY_CLIENT_SECRET")
            and self.data.get("SPOTIFY_REDIRECT_URI")
        )

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.data, handle, indent=4)
        except Exception as error:
            print(f"[!] Failed to save config: {error}")


class SpotifyCache:
    def __init__(self, path: str = CACHE_FILE) -> None:
        self.path = path
        self.mapping: Dict[str, Dict[str, str]] = self._load_cache()

    def _load_cache(self) -> Dict[str, Dict[str, str]]:
        if not os.path.exists(self.path):
            return {}

        try:
            with open(self.path, "r", encoding="utf-8") as handle:
                data = json.load(handle)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return {}

    def get(self, key: str) -> Optional[str]:
        entry = self.mapping.get(key)
        if not isinstance(entry, dict):
            return None
        uri = entry.get("uri")
        cached_at = entry.get("cached_at")
        if not uri or not cached_at:
            return None
        try:
            cached_date = datetime.fromisoformat(cached_at).date()
            if (datetime.now().date() - cached_date).days > CACHE_TTL_DAYS:
                del self.mapping[key]
                return None
            return uri
        except Exception:
            return None

    def set(self, key: str, uri: str) -> None:
        self.mapping[key] = {
            "uri": uri,
            "cached_at": datetime.now().date().isoformat(),
        }

    def save(self) -> None:
        try:
            with open(self.path, "w", encoding="utf-8") as handle:
                json.dump(self.mapping, handle, indent=2)
        except Exception as error:
            print(f"[!] Failed to save Spotify cache: {error}")


class MetadataExtractor:
    @staticmethod
    def parse_audio_tags(file_path: str, extension: str, cancel_event: Optional[threading.Event] = None) -> Tuple[str, str]:
        if cancel_event and cancel_event.is_set():
            return "", ""

        artist = ""
        title = ""
        try:
            if extension == ".mp3" and EasyID3 is not None:
                audio = EasyID3(file_path)
                artist = audio.get("artist", [""])[0]
                title = audio.get("title", [""])[0]
            elif File is not None:
                audio = File(file_path)
                if audio is not None:
                    if cancel_event and cancel_event.is_set():
                        return "", ""
                    if "artist" in audio:
                        artist = audio["artist"][0]
                    elif "\xa9ART" in audio:
                        artist = audio["\xa9ART"][0]
                    if "title" in audio:
                        title = audio["title"][0]
                    elif "\xa9nam" in audio:
                        title = audio["\xa9nam"][0]
        except Exception:
            pass
        return artist.strip(), title.strip()

    @staticmethod
    def parse_audio_filename(file_name: str) -> Tuple[str, str]:
        base_name = os.path.splitext(os.path.basename(file_name))[0].strip()

        for pattern in FILENAME_PATTERNS:
            match = pattern.match(base_name)
            if match:
                artist = match.groupdict().get("artist", "").strip()
                title = match.groupdict().get("title", "").strip()
                if artist and title:
                    return artist, title

        normalized_name = re.sub(r"[\s_]+", " ", base_name)
        if " - " in normalized_name:
            parts = normalized_name.split(" - ", 1)
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()

        if " _ " in normalized_name:
            parts = normalized_name.split(" _ ", 1)
            if len(parts) == 2:
                return parts[0].strip(), parts[1].strip()

        return "", base_name.replace("_", " ")

    @staticmethod
    def build_search_query(track_text: str) -> str:
        artist, title = MetadataExtractor.parse_audio_filename(track_text)
        if artist and title:
            return f"artist:{artist} track:{title}"
        return track_text


class SpotifyService:
    def __init__(self, config: Dict[str, str], logger: "AppLogger", cache: SpotifyCache) -> None:
        self.config = config
        self.logger = logger
        self.cache = cache
        self.spotify = self._authenticate()
        self.user_id = self._get_user_id() if self.spotify else ""
        self.playlist_cache: Dict[str, Dict[str, str]] = self._load_playlist_cache() if self.spotify else {}

    def _load_playlist_cache(self) -> Dict[str, Dict[str, str]]:
        playlist_map: Dict[str, Dict[str, str]] = {}
        try:
            offset = 0
            while True:
                response = self.spotify.current_user_playlists(limit=50, offset=offset)
                items = response.get("items", [])
                for item in items:
                    name = item.get("name", "").strip().lower()
                    if name:
                        playlist_map[name] = item
                if response.get("next") is None:
                    break
                offset += 50
        except Exception as error:
            self.logger.log(f"Could not load playlist cache: {error}")
        return playlist_map

    def _authenticate(self):
        if spotipy is None:
            self.logger.log("Missing dependency: spotipy is not installed.")
            return None

        try:
            try:
                import spotipy.oauth2 as oauth2

                def _gui_parse_response(self_oauth, url):
                    try:
                        prompt_text = (
                            "Please authorize Tag2Tune in your browser, then copy the ENTIRE URL "
                            "of the page you are redirected to and paste it below:\n\n" + url
                        )
                        redirected = simpledialog.askstring("Spotify Authentication", prompt_text)
                        if not redirected:
                            return None
                        parsed = urlparse.urlparse(redirected)
                        qs = urlparse.parse_qs(parsed.query)
                        return qs.get("code", [None])[0]
                    except Exception:
                        return None

                oauth2.SpotifyOAuth.parse_response_code = _gui_parse_response
            except Exception:
                pass

            auth_manager = SpotifyOAuth(
                client_id=self.config["SPOTIFY_CLIENT_ID"],
                client_secret=self.config["SPOTIFY_CLIENT_SECRET"],
                redirect_uri=self.config["SPOTIFY_REDIRECT_URI"],
                scope="playlist-modify-private playlist-modify-public playlist-read-private",
                cache_path=OAUTH_CACHE,
            )
            return spotipy.Spotify(auth_manager=auth_manager)
        except Exception as error:
            self.logger.log(f"Spotify authentication failed: {error}")
            return None

    def _get_user_id(self) -> str:
        if not self.spotify:
            return ""
        try:
            return self.spotify.current_user()["id"]
        except Exception as error:
            self.logger.log(f"Could not resolve Spotify user ID: {error}")
            return ""

    def search_track(self, track_text: str) -> Tuple[Optional[str], bool]:
        normalized_text = track_text.strip()
        if not normalized_text:
            return None, False

        cache_key = normalized_text
        cached_uri = self.cache.get(cache_key)
        if cached_uri:
            self.logger.log(f"Cache hit for: {normalized_text}")
            return cached_uri, True

        if not self.spotify:
            return None, False

        query = MetadataExtractor.build_search_query(normalized_text)
        for attempt in [query, normalized_text]:
            try:
                response = self.spotify.search(q=attempt, type="track", limit=1)
                items = response.get("tracks", {}).get("items", [])
                if items:
                    uri = items[0].get("uri")
                    if uri:
                        self.cache.set(cache_key, uri)
                        return uri, False
            except Exception as error:
                self.logger.log(f"Spotify search failed for '{attempt}': {error}")
                break
        return None, False

    def find_playlist_by_name(self, playlist_name: str) -> Optional[Dict[str, str]]:
        if not self.spotify:
            return None
        return self.playlist_cache.get(playlist_name.strip().lower())

    def get_user_playlists(self) -> List[Dict]:
        if not self.spotify:
            return []
        playlists = []
        try:
            offset = 0
            while True:
                response = self.spotify.current_user_playlists(limit=50, offset=offset)
                playlists.extend(response.get("items", []))
                if response.get("next") is None:
                    break
                offset += 50
        except Exception as error:
            self.logger.log(f"Error fetching user playlists: {error}")
        return playlists

    def create_playlist(self, playlist_name: str) -> Optional[str]:
        if not self.spotify or not self.user_id:
            return None
        try:
            playlist = self.spotify.user_playlist_create(
                user=self.user_id,
                name=playlist_name,
                public=False,
                description="Generated by Tag2Tune",
            )
            playlist_id = playlist.get("id")
            if playlist_id:
                self.playlist_cache[playlist_name.strip().lower()] = playlist
            return playlist_id
        except Exception as error:
            self.logger.log(f"Failed to create playlist '{playlist_name}': {error}")
            return None

    def add_playlist_items(self, playlist_id: str, uris: List[str], progress_callback=None) -> bool:
        if not self.spotify:
            return False
        try:
            for start in range(0, len(uris), 100):
                self.spotify.playlist_add_items(
                    playlist_id=playlist_id,
                    items=uris[start : start + 100],
                )
                if progress_callback:
                    progress_callback(min(start + 100, len(uris)), len(uris))
            return True
        except Exception as error:
            self.logger.log(f"Failed to add items to playlist '{playlist_id}': {error}")
            return False

    def get_playlist_tracks(self, playlist_id: str) -> List[Dict[str, object]]:
        if not self.spotify:
            return []
        tracks: List[Dict[str, object]] = []
        try:
            offset = 0
            while True:
                response = self.spotify.playlist_items(
                    playlist_id,
                    fields="items.track.uri,items.track.name,items.track.artists(name),next",
                    limit=100,
                    offset=offset,
                )
                items = response.get("items", [])
                for item in items:
                    track = item.get("track")
                    if track:
                        tracks.append(track)
                if response.get("next") is None:
                    break
                offset += 100
        except Exception as error:
            self.logger.log(f"Failed to retrieve tracks for playlist '{playlist_id}': {error}")
        return tracks

    def remove_playlist_items(self, playlist_id: str, tracks: List[Dict[str, object]]) -> bool:
        if not self.spotify:
            return False
        if not tracks:
            return True
        try:
            for start in range(0, len(tracks), 100):
                batch = tracks[start : start + 100]
                self.spotify.playlist_remove_specific_occurrences_of_items(
                    playlist_id,
                    items=batch,
                )
            return True
        except Exception as error:
            self.logger.log(f"Failed to remove duplicate tracks from playlist '{playlist_id}': {error}")
            return False


class AppLogger:
    def __init__(self, queue: queue.Queue, manager: LogManager) -> None:
        self.queue = queue
        self.manager = manager

    def log(self, message: str) -> None:
        self.queue.put(message)
        self.manager.write(message)


class PlaylistDetailsDialog(simpledialog.Dialog):
    def __init__(self, parent, title, initial_name):
        self.initial_name = initial_name
        self.result = None
        super().__init__(parent, title)

    def body(self, master):
        ttk.Label(master, text="Playlist Name:").grid(row=0, column=0, sticky="w", pady=4)
        self.name_entry = ttk.Entry(master, width=40)
        self.name_entry.insert(0, self.initial_name)
        self.name_entry.grid(row=0, column=1, pady=4, padx=5)

        ttk.Label(master, text="Description:").grid(row=1, column=0, sticky="w", pady=4)
        self.desc_entry = ttk.Entry(master, width=40)
        self.desc_entry.insert(0, "Generated by Tag2Tune")
        self.desc_entry.grid(row=1, column=1, pady=4, padx=5)

        self.public_var = tk.BooleanVar(value=False)
        self.public_check = ttk.Checkbutton(master, text="Make Playlist Public", variable=self.public_var)
        self.public_check.grid(row=2, column=0, columnspan=2, sticky="w", pady=6)
        
        return self.name_entry

    def apply(self):
        self.result = {
            "name": self.name_entry.get().strip() or self.initial_name,
            "description": self.desc_entry.get().strip(),
            "public": self.public_var.get(),
        }


class PlaylistImportDestinationDialog(simpledialog.Dialog):
    def __init__(self, parent):
        self.selected_option = "new"
        super().__init__(parent, "Import Destination")

    def body(self, master):
        ttk.Label(master, text="Choose how to import this playlist:", font=("Segoe UI", 10, "bold")).grid(
            row=0, column=0, columnspan=2, sticky="w", pady=(10, 8), padx=10
        )
        self.destination_var = tk.StringVar(value="new")
        ttk.Radiobutton(
            master,
            text="Create New Playlist",
            variable=self.destination_var,
            value="new",
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=20, pady=4)
        ttk.Radiobutton(
            master,
            text="Add To Existing Playlist",
            variable=self.destination_var,
            value="existing",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=20, pady=4)
        ttk.Label(
            master,
            text="Selecting an existing playlist will append tracks without removing current songs.",
            wraplength=360,
            font=("Segoe UI", 9),
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=20, pady=(8, 10))
        return None

    def apply(self):
        self.selected_option = self.destination_var.get()


class ExistingPlaylistConflictDialog(simpledialog.Dialog):
    def __init__(self, parent, playlist_name):
        self.playlist_name = playlist_name
        self.result = None
        super().__init__(parent, f"Playlist '{playlist_name}' Already Exists")

    def body(self, master):
        ttk.Label(
            master,
            text=f"Playlist '{self.playlist_name}' already exists.",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(10, 4), padx=10)
        ttk.Label(master, text="Choose how to proceed:", font=("Segoe UI", 9)).grid(
            row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8)
        )
        self.choice_var = tk.StringVar(value="add")
        ttk.Radiobutton(
            master,
            text="Add songs to the existing playlist",
            variable=self.choice_var,
            value="add",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=20, pady=2)
        ttk.Radiobutton(
            master,
            text="Rename playlist and create a new copy",
            variable=self.choice_var,
            value="rename",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=20, pady=2)
        ttk.Radiobutton(
            master,
            text="Cancel import",
            variable=self.choice_var,
            value="cancel",
        ).grid(row=4, column=0, columnspan=2, sticky="w", padx=20, pady=2)
        return None

    def apply(self):
        self.result = self.choice_var.get()


class PlaylistSelectionDialog(simpledialog.Dialog):
    def __init__(self, parent, playlists):
        self.playlists = playlists
        self.filtered_playlists = playlists.copy()
        self.selected_playlist = None
        self.sort_reverse = {"name": False, "description": False}
        super().__init__(parent, f"Select Spotify Playlist ({len(playlists)} available)")

    def body(self, master):
        ttk.Label(
            master,
            text="Select a playlist to append tracks to, or Cancel to create a new playlist.",
            font=("Segoe UI", 10, "bold"),
        ).pack(pady=(10, 4), padx=10, anchor="w")

        search_frame = ttk.Frame(master)
        search_frame.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Label(search_frame, text="Search:").pack(side="left")
        self.search_var = tk.StringVar()
        search_entry = ttk.Entry(search_frame, textvariable=self.search_var)
        search_entry.pack(side="left", fill="x", expand=True, padx=(6, 0))
        self.search_var.trace_add("write", lambda *args: self._apply_filter())

        container = ttk.Frame(master)
        container.pack(fill="both", expand=True, padx=10, pady=(0, 10))

        columns = ("name", "description")
        self.tree = ttk.Treeview(
            container,
            columns=columns,
            show="headings",
            selectmode="browse",
            height=12,
        )
        self.tree.heading("name", text="Name", command=lambda: self.sort_column("name"))
        self.tree.heading("description", text="Description", command=lambda: self.sort_column("description"))
        self.tree.column("name", width=220, anchor="w")
        self.tree.column("description", width=380, anchor="w")
        self.tree.pack(side="left", fill="both", expand=True)

        scrollbar = ttk.Scrollbar(container, orient="vertical", command=self.tree.yview)
        scrollbar.pack(side="right", fill="y")
        self.tree.configure(yscrollcommand=scrollbar.set)

        self._populate_tree(self.filtered_playlists)
        return self.tree

    def _populate_tree(self, playlists):
        for item in self.tree.get_children(""):
            self.tree.delete(item)
        for index, playlist in enumerate(playlists):
            name = playlist.get("name", "Unnamed playlist")
            description = playlist.get("description", "") or "(no description)"
            if len(description) > 120:
                description = description[:117] + "..."
            self.tree.insert("", "end", iid=str(index), values=(name, description))

    def _apply_filter(self):
        query = self.search_var.get().strip().lower()
        if not query:
            self.filtered_playlists = self.playlists.copy()
        else:
            self.filtered_playlists = [
                p
                for p in self.playlists
                if query in p.get("name", "").lower() or query in (p.get("description", "") or "").lower()
            ]
        self._populate_tree(self.filtered_playlists)

    def sort_column(self, col):
        data = [(self.tree.set(item, col).lower(), item) for item in self.tree.get_children("")]
        data.sort(reverse=self.sort_reverse[col])
        for index, (_, item) in enumerate(data):
            self.tree.move(item, "", index)
        self.sort_reverse[col] = not self.sort_reverse[col]

    def apply(self):
        selection = self.tree.selection()
        if selection:
            index = int(selection[0])
            self.selected_playlist = self.filtered_playlists[index]


class DuplicateResolverDialog(simpledialog.Dialog):
    def __init__(self, parent, playlist_name, duplicates):
        self.playlist_name = playlist_name
        self.duplicates = duplicates
        self.result = None
        super().__init__(parent, f"Remove Duplicate Tracks — {playlist_name}")

    def body(self, master):
        total_duplicates = sum(item.get("count", 0) for item in self.duplicates)
        ttk.Label(
            master,
            text=f"Duplicate tracks found in '{self.playlist_name}'",
            font=("Segoe UI", 10, "bold"),
        ).grid(row=0, column=0, columnspan=2, sticky="w", pady=(10, 4), padx=10)
        ttk.Label(
            master,
            text=f"{len(self.duplicates)} unique duplicate tracks found ({total_duplicates} duplicate copies).",
            font=("Segoe UI", 9),
        ).grid(row=1, column=0, columnspan=2, sticky="w", padx=10, pady=(0, 8))

        self.choice_var = tk.StringVar(value="keep_first")
        ttk.Radiobutton(
            master,
            text="Keep first occurrence of each duplicate track and remove later copies.",
            variable=self.choice_var,
            value="keep_first",
        ).grid(row=2, column=0, columnspan=2, sticky="w", padx=20, pady=2)
        ttk.Radiobutton(
            master,
            text="Keep last occurrence of each duplicate track and remove earlier copies.",
            variable=self.choice_var,
            value="keep_last",
        ).grid(row=3, column=0, columnspan=2, sticky="w", padx=20, pady=2)

        details = scrolledtext.ScrolledText(master, width=78, height=10, wrap="word")
        details.grid(row=4, column=0, columnspan=2, padx=10, pady=(8, 4))
        details.configure(state="normal")
        for item in self.duplicates:
            name = item.get("name", "Unknown track")
            artist = item.get("artist", "Unknown artist")
            count = item.get("count", 0)
            details.insert("end", f"{name} — {artist} ({count} copies)\n")
        details.configure(state="disabled")
        return None

    def apply(self):
        self.result = self.choice_var.get()


class Tag2TuneApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Tag2Tune — Local Music to Spotify")
        self.root.geometry("1100x760")
        self.root.minsize(940, 640)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(5, weight=1)

        self.config_manager = ConfigManager()
        self.log_manager = LogManager(LOG_FOLDER)
        self.log_queue: queue.Queue = queue.Queue()
        self.ui_tasks: queue.Queue = queue.Queue()
        self.logger = AppLogger(self.log_queue, self.log_manager)
        self.cache = SpotifyCache()
        self.cancel_scan_event = threading.Event()
        self.cancel_sync_event = threading.Event()
        self.active_thread: Optional[threading.Thread] = None

        self.music_folder_var = tk.StringVar(value=self.config_manager.data.get("DEFAULT_MUSIC_DIRECTORY", ""))
        self.playlist_source_var = tk.StringVar()
        self.sync_mode_var = tk.StringVar(value="Playlist Folder")
        self.spotify_status_var = tk.StringVar()
        self.cache_status_var = tk.StringVar()
        self.scan_summary_var = tk.StringVar(value="No scan performed yet.")
        self.current_task_var = tk.StringVar(value="Ready to start.")
        self.progress_detail_var = tk.StringVar(value="")
        self.progress_percent_var = tk.StringVar(value="0%")
        self.update_existing_var = tk.BooleanVar(value=True)
        self.show_console_var = tk.BooleanVar(value=True)
        self.format_var = tk.StringVar(value="All Audio Formats")
        self.settings_window: Optional[tk.Toplevel] = None

        self._build_ui()
        self._schedule_ui_tasks()
        self._refresh_status_bar()
        self.logger.log("Application started.")

    def _build_ui(self) -> None:
        self._configure_styles()
        self._build_menu()

        self.root.configure(background=self.spotify_black)
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(0, weight=0)
        self.root.rowconfigure(1, weight=0)
        self.root.rowconfigure(2, weight=0)
        self.root.rowconfigure(3, weight=0)
        self.root.rowconfigure(4, weight=0)
        self.root.rowconfigure(5, weight=1)
        self.root.rowconfigure(6, weight=0)

        self._build_header()
        self._build_dashboard()
        self._build_toolbar()
        self._build_main_content()
        self._build_progress_card()
        self._build_console()
        self._build_statusbar()

        self._refresh_sync_mode_ui()
        sys.stdout = self

    def _configure_styles(self) -> None:
        self.spotify_black = "#121212"
        self.spotify_gray = "#181818"
        self.spotify_raised = "#1E1E1E"
        self.spotify_border = "#2A2A2A"
        self.spotify_entry = "#202020"
        self.spotify_green = "#1DB954"
        self.spotify_hover_green = "#28D864"
        self.spotify_text = "#FFFFFF"
        self.spotify_muted = "#B3B3B3"
        self.spotify_disabled = "#666666"
        self.spotify_danger = "#7A2E36"
        self.spotify_danger_hover = "#9A3A44"

        self.root.option_add("*tearOff", False)
        self.root.option_add("*Font", ("Segoe UI", 10))

        self.style = ttk.Style(self.root)
        try:
            self.style.theme_use("clam")
        except Exception:
            pass

        self.style.configure(".", background=self.spotify_black, foreground=self.spotify_text, font=("Segoe UI", 10))
        self.style.configure("TFrame", background=self.spotify_black, borderwidth=0)
        self.style.configure("Header.TFrame", background=self.spotify_black, borderwidth=0)
        self.style.configure("Card.TFrame", background=self.spotify_gray, borderwidth=1, relief="solid")
        self.style.configure("CardBody.TFrame", background=self.spotify_gray, borderwidth=0)
        self.style.configure("Raised.Card.TFrame", background=self.spotify_raised, borderwidth=1, relief="solid")
        self.style.configure("Status.TFrame", background=self.spotify_gray, borderwidth=1, relief="solid")
        self.style.configure("Toolbar.TFrame", background=self.spotify_black, borderwidth=0)
        self.style.configure("Statusbar.TFrame", background=self.spotify_black, borderwidth=0)

        self.style.configure("TLabel", background=self.spotify_black, foreground=self.spotify_text, padding=0)
        self.style.configure("Title.TLabel", background=self.spotify_black, foreground=self.spotify_text, font=("Segoe UI Semibold", 22))
        self.style.configure("Subtitle.TLabel", background=self.spotify_black, foreground=self.spotify_muted, font=("Segoe UI", 10))
        self.style.configure("Section.TLabel", background=self.spotify_gray, foreground=self.spotify_text, font=("Segoe UI Semibold", 13))
        self.style.configure("RaisedSection.TLabel", background=self.spotify_raised, foreground=self.spotify_text, font=("Segoe UI Semibold", 13))
        self.style.configure("CardTitle.TLabel", background=self.spotify_gray, foreground=self.spotify_muted, font=("Segoe UI Semibold", 10))
        self.style.configure("CardValue.TLabel", background=self.spotify_gray, foreground=self.spotify_text, font=("Segoe UI Semibold", 13))
        self.style.configure("CardMuted.TLabel", background=self.spotify_gray, foreground=self.spotify_muted, font=("Segoe UI", 9))
        self.style.configure("Label.TLabel", background=self.spotify_gray, foreground=self.spotify_muted, font=("Segoe UI", 10))
        self.style.configure("Status.TLabel", background=self.spotify_black, foreground=self.spotify_muted, font=("Segoe UI", 9))
        self.style.configure("ProgressTitle.TLabel", background=self.spotify_gray, foreground=self.spotify_text, font=("Segoe UI Semibold", 13))
        self.style.configure("ProgressDetail.TLabel", background=self.spotify_gray, foreground=self.spotify_muted, font=("Segoe UI", 10))
        self.style.configure("ProgressPercent.TLabel", background=self.spotify_gray, foreground=self.spotify_green, font=("Segoe UI Semibold", 13))

        self.style.configure("TButton", borderwidth=0, focusthickness=0, padding=(16, 10), font=("Segoe UI Semibold", 10))
        self.style.configure("Primary.TButton", background=self.spotify_green, foreground="black")
        self.style.map(
            "Primary.TButton",
            background=[("disabled", self.spotify_raised), ("pressed", "#169C46"), ("active", self.spotify_hover_green)],
            foreground=[("disabled", self.spotify_disabled), ("pressed", "black"), ("active", "black")],
        )
        self.style.configure("Secondary.TButton", background=self.spotify_raised, foreground=self.spotify_text)
        self.style.map(
            "Secondary.TButton",
            background=[("disabled", self.spotify_gray), ("pressed", "#2B2B2B"), ("active", "#333333")],
            foreground=[("disabled", self.spotify_disabled), ("active", self.spotify_text)],
        )
        self.style.configure("Danger.TButton", background=self.spotify_danger, foreground=self.spotify_text)
        self.style.map(
            "Danger.TButton",
            background=[("disabled", self.spotify_gray), ("pressed", "#61262D"), ("active", self.spotify_danger_hover)],
            foreground=[("disabled", self.spotify_disabled), ("active", self.spotify_text)],
        )
        self.style.configure("Toolbar.TButton", background=self.spotify_raised, foreground=self.spotify_text, padding=(18, 14), font=("Segoe UI Semibold", 10))
        self.style.map(
            "Toolbar.TButton",
            background=[("disabled", self.spotify_gray), ("pressed", "#2B2B2B"), ("active", "#333333")],
            foreground=[("disabled", self.spotify_disabled), ("active", self.spotify_text)],
        )

        self.style.configure("TCheckbutton", background=self.spotify_gray, foreground=self.spotify_text, padding=(0, 6), font=("Segoe UI", 10))
        self.style.map(
            "TCheckbutton",
            background=[("active", self.spotify_gray)],
            foreground=[("disabled", self.spotify_disabled), ("active", self.spotify_text)],
        )
        self.style.configure("ConsoleToggle.TCheckbutton", background=self.spotify_gray, foreground=self.spotify_muted, padding=(0, 4), font=("Segoe UI Semibold", 9))
        self.style.map("ConsoleToggle.TCheckbutton", background=[("active", self.spotify_gray)], foreground=[("active", self.spotify_text)])

        self.style.configure("Modern.TEntry", fieldbackground=self.spotify_entry, background=self.spotify_entry, foreground=self.spotify_text, insertcolor=self.spotify_text, borderwidth=1, relief="flat", padding=(10, 8))
        self.style.map(
            "Modern.TEntry",
            fieldbackground=[("readonly", self.spotify_entry), ("disabled", self.spotify_gray)],
            foreground=[("readonly", self.spotify_text), ("disabled", self.spotify_disabled)],
        )
        self.style.configure("Modern.TCombobox", fieldbackground=self.spotify_entry, background=self.spotify_entry, foreground=self.spotify_text, arrowcolor=self.spotify_text, borderwidth=1, relief="flat", padding=(10, 8))
        self.style.map(
            "Modern.TCombobox",
            fieldbackground=[("readonly", self.spotify_entry), ("disabled", self.spotify_gray)],
            foreground=[("readonly", self.spotify_text), ("disabled", self.spotify_disabled)],
            selectbackground=[("readonly", self.spotify_entry)],
            selectforeground=[("readonly", self.spotify_text)],
        )

        self.style.configure("Modern.Horizontal.TProgressbar", troughcolor=self.spotify_entry, background=self.spotify_green, bordercolor=self.spotify_entry, lightcolor=self.spotify_green, darkcolor=self.spotify_green, thickness=24)
        self.style.configure("Treeview", background=self.spotify_gray, fieldbackground=self.spotify_gray, foreground=self.spotify_text, bordercolor=self.spotify_black, lightcolor=self.spotify_black, darkcolor=self.spotify_black, rowheight=26)
        self.style.configure("Treeview.Heading", background=self.spotify_raised, foreground=self.spotify_text, font=("Segoe UI Semibold", 10))
        self.style.configure("Vertical.TScrollbar", background=self.spotify_gray, troughcolor=self.spotify_black, arrowcolor=self.spotify_text)

    def _build_menu(self) -> None:
        menu_bar = tk.Menu(self.root, background=self.spotify_black, foreground=self.spotify_text, activebackground=self.spotify_green, activeforeground="black")
        file_menu = tk.Menu(menu_bar, tearoff=0, background=self.spotify_black, foreground=self.spotify_text, activebackground=self.spotify_green, activeforeground="black")
        file_menu.add_command(label="Generate Playlist Files", command=self.on_scan_clicked)
        file_menu.add_command(label="Import Playlists", command=self.on_sync_clicked)
        file_menu.add_separator()
        file_menu.add_command(label="Exit", command=self.root.quit)
        menu_bar.add_cascade(label="File", menu=file_menu)

        tools_menu = tk.Menu(menu_bar, tearoff=0, background=self.spotify_black, foreground=self.spotify_text, activebackground=self.spotify_green, activeforeground="black")
        tools_menu.add_command(label="Spotify Settings", command=self.open_settings_window)
        tools_menu.add_command(label="Remove Duplicates from Playlist", command=self.on_remove_duplicates_clicked)
        tools_menu.add_command(label="Clear Cache", command=self._clear_cache)
        tools_menu.add_command(label="Open Log Folder", command=self.open_log_folder)
        menu_bar.add_cascade(label="Tools", menu=tools_menu)

        help_menu = tk.Menu(menu_bar, tearoff=0, background=self.spotify_black, foreground=self.spotify_text, activebackground=self.spotify_green, activeforeground="black")
        help_menu.add_command(label="About", command=self._show_about)
        menu_bar.add_cascade(label="Help", menu=help_menu)
        self.root.config(menu=menu_bar)

    def _build_header(self) -> None:
        header_frame = ttk.Frame(self.root, style="Header.TFrame")
        header_frame.grid(row=0, column=0, sticky="ew", padx=24, pady=(20, 18))
        header_frame.columnconfigure(0, weight=1)

        ttk.Label(header_frame, text="Tag2Tune", style="Title.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(
            header_frame,
            text="Convert your local music library into Spotify playlists.",
            style="Subtitle.TLabel",
        ).grid(row=1, column=0, sticky="w", pady=(6, 14))

        rule = tk.Frame(header_frame, background=self.spotify_border, height=1, bd=0, highlightthickness=0)
        rule.grid(row=2, column=0, sticky="ew")

    def _build_dashboard(self) -> None:
        dashboard_frame = ttk.Frame(self.root, style="TFrame")
        dashboard_frame.grid(row=1, column=0, sticky="ew", padx=24, pady=(0, 18))
        dashboard_frame.columnconfigure((0, 1, 2), weight=1, uniform="dashboard")

        self._build_dashboard_card(dashboard_frame, 0, "Spotify", self.spotify_status_var, "Authentication status")
        self._build_dashboard_card(dashboard_frame, 1, "Cache", self.cache_status_var, "Spotify lookup cache")
        self._build_dashboard_card(dashboard_frame, 2, "Library", self.scan_summary_var, "Selected music source")

    def _build_dashboard_card(self, parent: ttk.Frame, column: int, title: str, variable: tk.StringVar, subtitle: str) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=(18, 14))
        card.grid(row=0, column=column, sticky="nsew", padx=(0 if column == 0 else 8, 0 if column == 2 else 8))
        card.columnconfigure(0, weight=1)
        ttk.Label(card, text=title, style="CardTitle.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(card, textvariable=variable, style="CardValue.TLabel", wraplength=280).grid(row=1, column=0, sticky="ew", pady=(8, 0))
        ttk.Label(card, text=subtitle, style="CardMuted.TLabel").grid(row=2, column=0, sticky="w", pady=(8, 0))

    def _build_toolbar(self) -> None:
        toolbar_frame = ttk.Frame(self.root, style="Toolbar.TFrame")
        toolbar_frame.grid(row=2, column=0, sticky="ew", padx=24, pady=(0, 20))
        toolbar_frame.columnconfigure((0, 1, 2, 3), weight=1, uniform="toolbar")

        buttons = [
            ("Generate Playlist", self.on_scan_clicked),
            ("Import into Spotify", self.on_sync_clicked),
            ("Open Logs", self.open_log_folder),
            ("Settings", self.open_settings_window),
        ]
        for column, (text, command) in enumerate(buttons):
            button = ttk.Button(toolbar_frame, text=text, command=command, style="Toolbar.TButton")
            button.grid(row=0, column=column, sticky="ew", padx=(0 if column == 0 else 8, 0 if column == 3 else 8), ipady=2)
            self._register_button_hover(button)

    def _build_main_content(self) -> None:
        content_frame = ttk.Frame(self.root, style="TFrame")
        content_frame.grid(row=3, column=0, sticky="ew", padx=24, pady=(0, 20))
        content_frame.columnconfigure(0, weight=1, uniform="content")
        content_frame.columnconfigure(1, weight=1, uniform="content")

        self._build_music_card(content_frame)
        self._build_import_card(content_frame)

    def _build_music_card(self, parent: ttk.Frame) -> None:
        music_frame = ttk.Frame(parent, style="Card.TFrame", padding=(20, 18))
        music_frame.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        music_frame.columnconfigure(0, weight=1)

        ttk.Label(music_frame, text="Music Library", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(music_frame, text="Choose a folder and generate playlist text files.", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 18))

        ttk.Label(music_frame, text="Music Folder", style="Label.TLabel").grid(row=2, column=0, sticky="w")
        folder_row = ttk.Frame(music_frame, style="CardBody.TFrame")
        folder_row.grid(row=3, column=0, sticky="ew", pady=(6, 16))
        folder_row.columnconfigure(0, weight=1)
        self.music_folder_entry = ttk.Entry(folder_row, textvariable=self.music_folder_var, state="readonly", style="Modern.TEntry")
        self.music_folder_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        browse_music_button = ttk.Button(folder_row, text="Browse", command=self.on_browse_music_folder, style="Secondary.TButton", width=12)
        browse_music_button.grid(row=0, column=1, sticky="e")
        self._register_button_hover(browse_music_button)

        ttk.Label(music_frame, text="Format", style="Label.TLabel").grid(row=4, column=0, sticky="w")
        self.format_dropdown = ttk.Combobox(music_frame, textvariable=self.format_var, state="readonly", values=list(SUPPORTED_EXTENSIONS.keys()), style="Modern.TCombobox")
        self.format_dropdown.grid(row=5, column=0, sticky="ew", pady=(6, 18))

        action_row = ttk.Frame(music_frame, style="CardBody.TFrame")
        action_row.grid(row=6, column=0, sticky="ew")
        action_row.columnconfigure((0, 1), weight=1, uniform="music_actions")
        self.generate_button = ttk.Button(action_row, text="Generate", command=self.on_scan_clicked, style="Primary.TButton")
        self.generate_button.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=2)
        self.cancel_scan_button = ttk.Button(action_row, text="Cancel", command=self.on_cancel_scan, state="disabled", style="Danger.TButton")
        self.cancel_scan_button.grid(row=0, column=1, sticky="ew", padx=(8, 0), ipady=2)
        self._register_button_hover(self.generate_button)
        self._register_button_hover(self.cancel_scan_button)

    def _build_import_card(self, parent: ttk.Frame) -> None:
        sync_frame = ttk.Frame(parent, style="Card.TFrame", padding=(20, 18))
        sync_frame.grid(row=0, column=1, sticky="nsew", padx=(10, 0))
        sync_frame.columnconfigure(0, weight=1)

        ttk.Label(sync_frame, text="Spotify Import", style="Section.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(sync_frame, text="Import generated playlist files into Spotify.", style="CardMuted.TLabel").grid(row=1, column=0, sticky="w", pady=(4, 18))

        self.playlist_source_label = ttk.Label(sync_frame, text="Playlist Folder", style="Label.TLabel")
        self.playlist_source_label.grid(row=2, column=0, sticky="w")
        source_row = ttk.Frame(sync_frame, style="CardBody.TFrame")
        source_row.grid(row=3, column=0, sticky="ew", pady=(6, 16))
        source_row.columnconfigure(0, weight=1)
        self.playlist_source_entry = ttk.Entry(source_row, textvariable=self.playlist_source_var, state="readonly", style="Modern.TEntry")
        self.playlist_source_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.browse_playlist_button = ttk.Button(source_row, text="Browse", command=self.on_browse_playlist_source, style="Secondary.TButton", width=12)
        self.browse_playlist_button.grid(row=0, column=1, sticky="e")
        self._register_button_hover(self.browse_playlist_button)

        mode_row = ttk.Frame(sync_frame, style="CardBody.TFrame")
        mode_row.grid(row=4, column=0, sticky="ew", pady=(0, 16))
        mode_row.columnconfigure(0, weight=1)
        mode_row.columnconfigure(1, weight=1)
        ttk.Label(mode_row, text="Import Mode", style="Label.TLabel").grid(row=0, column=0, sticky="w")
        ttk.Label(mode_row, text="Existing Playlists", style="Label.TLabel").grid(row=0, column=1, sticky="w", padx=(16, 0))
        self.sync_mode_dropdown = ttk.Combobox(mode_row, textvariable=self.sync_mode_var, state="readonly", values=["Playlist Folder", "Single Playlist File"], style="Modern.TCombobox")
        self.sync_mode_dropdown.grid(row=1, column=0, sticky="ew", pady=(6, 0), padx=(0, 8))
        self.sync_mode_dropdown.bind("<<ComboboxSelected>>", lambda event: self._refresh_sync_mode_ui())
        self.update_existing_checkbox = ttk.Checkbutton(mode_row, text="Update Existing", variable=self.update_existing_var)
        self.update_existing_checkbox.grid(row=1, column=1, sticky="w", padx=(16, 0), pady=(6, 0))

        action_row = ttk.Frame(sync_frame, style="CardBody.TFrame")
        action_row.grid(row=5, column=0, sticky="ew", pady=(2, 0))
        action_row.columnconfigure((0, 1), weight=1, uniform="sync_actions")
        self.sync_button = ttk.Button(action_row, text="Import", command=self.on_sync_clicked, style="Primary.TButton")
        self.sync_button.grid(row=0, column=0, sticky="ew", padx=(0, 8), ipady=2)
        self.cancel_sync_button = ttk.Button(action_row, text="Cancel", command=self.on_cancel_sync, state="disabled", style="Danger.TButton")
        self.cancel_sync_button.grid(row=0, column=1, sticky="ew", padx=(8, 0), ipady=2)
        self._register_button_hover(self.sync_button)
        self._register_button_hover(self.cancel_sync_button)

    def _build_progress_card(self) -> None:
        progress_frame = ttk.Frame(self.root, style="Status.TFrame", padding=(20, 16))
        progress_frame.grid(row=4, column=0, sticky="ew", padx=24, pady=(0, 20))
        progress_frame.columnconfigure(0, weight=1)
        progress_frame.columnconfigure(1, weight=0)

        self.current_task_label = ttk.Label(progress_frame, textvariable=self.current_task_var, style="ProgressTitle.TLabel")
        self.current_task_label.grid(row=0, column=0, sticky="w")
        self.progress_percent_label = ttk.Label(progress_frame, textvariable=self.progress_percent_var, style="ProgressPercent.TLabel")
        self.progress_percent_label.grid(row=0, column=1, sticky="e", padx=(18, 0))

        self.progress_bar = ttk.Progressbar(progress_frame, mode="determinate", style="Modern.Horizontal.TProgressbar")
        self.progress_bar.grid(row=1, column=0, columnspan=2, sticky="ew", pady=(12, 8))

        self.progress_detail_label = ttk.Label(progress_frame, textvariable=self.progress_detail_var, style="ProgressDetail.TLabel")
        self.progress_detail_label.grid(row=2, column=0, sticky="w")

        self.console_toggle = ttk.Checkbutton(progress_frame, text="Hide Activity Log", variable=self.show_console_var, command=self.toggle_console, style="ConsoleToggle.TCheckbutton")
        self.console_toggle.grid(row=2, column=1, sticky="e")

    def _build_console(self) -> None:
        self.console_frame = ttk.Frame(self.root, style="Raised.Card.TFrame", padding=(14, 12))
        self.console_frame.grid(row=5, column=0, sticky="nsew", padx=24, pady=(0, 16))
        self.console_frame_grid_info = self.console_frame.grid_info()
        self.console_frame.columnconfigure(0, weight=1)
        self.console_frame.rowconfigure(1, weight=1)

        ttk.Label(self.console_frame, text="Activity Log", style="RaisedSection.TLabel").grid(row=0, column=0, sticky="w", pady=(0, 10))
        self.log_area = scrolledtext.ScrolledText(
            self.console_frame,
            wrap=tk.WORD,
            bg="#0D0D0D",
            fg="#E8E8E8",
            insertbackground="#FFFFFF",
            selectbackground=self.spotify_green,
            selectforeground="black",
            relief="flat",
            borderwidth=0,
            padx=12,
            pady=10,
            font=("Consolas", 10),
        )
        self.log_area.grid(row=1, column=0, sticky="nsew")
        self.log_area.configure(state="disabled")

    def _build_statusbar(self) -> None:
        status_frame = ttk.Frame(self.root, style="Statusbar.TFrame")
        status_frame.grid(row=6, column=0, sticky="ew", padx=24, pady=(0, 12))
        status_frame.columnconfigure(0, weight=1)
        self.status_label = ttk.Label(status_frame, text="Ready", style="Status.TLabel")
        self.status_label.grid(row=0, column=0, sticky="w")

    def _register_button_hover(self, button: ttk.Button) -> None:
        button.configure(cursor="hand2")
        button.bind("<Enter>", lambda event: button.state(["active"]), add="+")
        button.bind("<Leave>", lambda event: button.state(["!active"]), add="+")
    def write(self, message: str) -> None:
        if message:
            self.log_queue.put(message)
            self.log_manager.write(message)

    def flush(self) -> None:
        pass

    def _schedule_ui_tasks(self) -> None:
        self._process_log_queue()
        self._process_ui_tasks()

    def _process_log_queue(self) -> None:
        while not self.log_queue.empty():
            message = self.log_queue.get_nowait()
            self.log_area.configure(state="normal")
            self.log_area.insert(tk.END, f"{message}\n")
            self.log_area.see(tk.END)
            self.log_area.configure(state="disabled")
        self.root.after(100, self._process_log_queue)

    def _process_ui_tasks(self) -> None:
        while not self.ui_tasks.empty():
            task = self.ui_tasks.get_nowait()
            if task[0] == "prompt_playlist_update":
                _, playlist_name, event, result = task
                answer = messagebox.askyesno(
                    "Existing playlist found",
                    f"Playlist '{playlist_name}' already exists.\n\nSelect 'Yes' to update the existing playlist or 'No' to create a new playlist.",
                    icon=messagebox.QUESTION,
                )
                result["answer"] = answer
                event.set()
            elif task[0] == "prompt_playlist_details":
                _, default_name, event, result = task
                dialog = PlaylistDetailsDialog(self.root, "Playlist Configuration", default_name)
                result["details"] = dialog.result
                event.set()
            elif task[0] == "prompt_import_destination":
                _, playlist_name, event, result = task
                dialog = PlaylistImportDestinationDialog(self.root)
                result["destination"] = dialog.selected_option
                event.set()
            elif task[0] == "prompt_playlist_selection":
                _, playlists, event, result = task
                dialog = PlaylistSelectionDialog(self.root, playlists)
                result["selected_playlist"] = dialog.selected_playlist
                event.set()
            elif task[0] == "prompt_existing_playlist_conflict":
                _, playlist_name, event, result = task
                dialog = ExistingPlaylistConflictDialog(self.root, playlist_name)
                result["action"] = dialog.result
                event.set()
            elif task[0] == "prompt_duplicate_resolver":
                _, playlist_name, duplicates, event, result = task
                dialog = DuplicateResolverDialog(self.root, playlist_name, duplicates)
                result["action"] = dialog.result
                event.set()
            elif task[0] == "prompt_confirm_duplicate_removal":
                _, playlist_name, remove_count, event, result = task
                answer = messagebox.askyesno(
                    "Confirm Duplicate Removal",
                    f"Remove {remove_count} duplicate track occurrences from '{playlist_name}'?\n\nThis operation cannot be undone.",
                    icon=messagebox.WARNING,
                )
                result["confirmed"] = answer
                event.set()
            elif task[0] == "show_playlist_url":
                _, playlist_name, url = task
                url_win = tk.Toplevel(self.root)
                url_win.title("Playlist Created")
                url_win.geometry("520x170")
                url_win.transient(self.root)
                url_win.resizable(False, False)

                ttk.Label(url_win, text=f"'{playlist_name}' uploaded successfully!", font=("Segoe UI", 10, "bold")).pack(pady=8)

                entry_frame = ttk.Frame(url_win)
                entry_frame.pack(fill="x", padx=10)
                entry = ttk.Entry(entry_frame, width=60)
                entry.insert(0, url)
                entry.config(state="readonly")
                entry.pack(side="left", fill="x", expand=True)

                button_frame = ttk.Frame(url_win)
                button_frame.pack(fill="x", padx=10, pady=10)

                def open_spotify():
                    try:
                        webbrowser.open(url)
                    except Exception as err:
                        messagebox.showerror("Error", f"Unable to open link: {err}")

                def copy_link():
                    try:
                        self.root.clipboard_clear()
                        self.root.clipboard_append(url)
                        messagebox.showinfo("Link Copied", "Playlist URL copied to clipboard.")
                    except Exception as err:
                        messagebox.showerror("Error", f"Unable to copy link: {err}")

                ttk.Button(button_frame, text="Open in Spotify", command=open_spotify).pack(side="left", padx=(0, 4))
                ttk.Button(button_frame, text="Copy Link", command=copy_link).pack(side="left", padx=(0, 4))
                ttk.Button(button_frame, text="Close", command=url_win.destroy).pack(side="right")
            elif task[0] == "update_progress":
                _, value, maximum, text = task
                self.progress_bar.config(maximum=maximum, value=value)
                self.current_task_var.set(text)
                self.progress_detail_var.set(
                    f"{value} / {maximum} tracks" if maximum else ""
                )
                percent = int((value / maximum) * 100) if maximum else 0
                self.progress_percent_var.set(f"{percent}%")
            elif task[0] == "update_summary":
                _, text = task
                self.scan_summary_var.set(text)
            elif task[0] == "refresh_status":
                self._refresh_status_bar()
            elif task[0] == "prepare_scan_ui":
                _, running = task
                self._prepare_scan_ui(running)
            elif task[0] == "prepare_sync_ui":
                _, running = task
                self._prepare_sync_ui(running)
            elif task[0] == "notify":
                _, title, text = task
                try:
                    messagebox.showinfo(title, text)
                except Exception:
                    self.logger.log(text)
        self.root.after(100, self._process_ui_tasks)

    def _queue_ui_task(self, task_type: str, *args) -> None:
        self.ui_tasks.put((task_type, *args))

    def _refresh_status_bar(self) -> None:
        self.spotify_status_var.set(
            "🟢 Connected" if self.config_manager.is_valid() else "🔴 Not Configured"
        )
        self.cache_status_var.set(f"{len(self.cache.mapping)} Cached Tracks")
        self.scan_summary_var.set(
            f"Selected library: {self.music_folder_var.get() or 'None'}"
        )
        if not self.config_manager.is_valid():
            self.status_label.config(
                text="Spotify credentials are incomplete. Configure config.json before syncing."
            )
            self.sync_button.state(["disabled"])
        else:
            self.status_label.config(text="Ready")
            self.sync_button.state(["!disabled"])

    def on_browse_music_folder(self) -> None:
        directory = filedialog.askdirectory(
            title="Select Main Music Folder",
            initialdir=self.config_manager.data.get("DEFAULT_MUSIC_DIRECTORY", "") or os.getcwd(),
        )
        if not directory:
            return
        self.music_folder_var.set(directory)
        try:
            self.config_manager.data["DEFAULT_MUSIC_DIRECTORY"] = directory
            self.config_manager.save()
        except Exception:
            pass
        self._refresh_status_bar()

    def on_browse_playlist_source(self) -> None:
        mode = self.sync_mode_var.get()
        if mode == "Single Playlist File":
            selected = filedialog.askopenfilename(
                title="Select Generated Playlist Text File",
                filetypes=[("Text Files", "*.txt")],
            )
        else:
            selected = filedialog.askdirectory(
                title="Select Playlist Folder",
                initialdir=self.music_folder_var.get() or os.getcwd(),
            )
        if not selected:
            return
        self.playlist_source_var.set(selected)

    def _refresh_sync_mode_ui(self) -> None:
        mode = self.sync_mode_var.get()
        if mode == "Single Playlist File":
            self.playlist_source_label.config(text="Playlist File:")
            self.browse_playlist_button.config(text="Browse File...")
        else:
            self.playlist_source_label.config(text="Playlist Folder:")
            self.browse_playlist_button.config(text="Browse Folder...")

    def _clear_cache(self) -> None:
        self.cache.mapping.clear()
        self.cache.save()
        self._refresh_status_bar()
        self.logger.log("Spotify cache cleared.")

    def open_log_folder(self) -> None:
        try:
            if not os.path.exists(LOG_FOLDER):
                os.makedirs(LOG_FOLDER, exist_ok=True)
            system = platform.system()
            if system == "Windows":
                os.startfile(LOG_FOLDER)
            elif system == "Darwin":
                subprocess.Popen(["open", LOG_FOLDER])
            else:
                subprocess.Popen(["xdg-open", LOG_FOLDER])
        except Exception as error:
            messagebox.showerror("Error", f"Unable to open log folder: {error}")

    def _show_about(self) -> None:
        messagebox.showinfo(
            "About Tag2Tune",
            "Tag2Tune\nConvert local music playlists into Spotify playlists with a simplified workflow.",
        )

    def toggle_console(self) -> None:
        if self.show_console_var.get():
            self.console_frame.grid(
                **self.console_frame_grid_info,
            )
            self.console_toggle.config(text="Hide Activity Log")
        else:
            self.console_frame.grid_remove()
            self.console_toggle.config(text="Show Activity Log")
        self.root.update_idletasks()

    def on_scan_clicked(self) -> None:
        if self.active_thread and self.active_thread.is_alive():
            return
        directory = self.music_folder_var.get()
        if not directory:
            self.on_browse_music_folder()
            directory = self.music_folder_var.get()
        if not directory:
            return
        try:
            self.config_manager.data["DEFAULT_MUSIC_DIRECTORY"] = directory
            self.config_manager.save()
        except Exception:
            pass
        self.cancel_scan_event.clear()
        self._prepare_scan_ui(True)
        self.active_thread = threading.Thread(
            target=self._scan_worker,
            args=(directory, SUPPORTED_EXTENSIONS[self.format_var.get()]),
            daemon=True,
        )
        self.active_thread.start()

    def on_cancel_scan(self) -> None:
        self.cancel_scan_event.set()
        self.logger.log("Scan cancellation requested.")

    def on_sync_clicked(self) -> None:
        if self.active_thread and self.active_thread.is_alive():
            return
        mode = self.sync_mode_var.get()
        source = self.playlist_source_var.get().strip()
        paths: List[str] = []
        if not source:
            messagebox.showwarning(
                "Playlist Source Required",
                "Please select a playlist file or folder before importing into Spotify.",
            )
            return
        if mode == "Single Playlist File":
            if not os.path.isfile(source):
                messagebox.showwarning(
                    "Invalid File",
                    "The selected playlist file does not exist. Please choose a valid file.",
                )
                return
            paths = [source]
        else:
            if not os.path.isdir(source):
                messagebox.showwarning(
                    "Invalid Folder",
                    "The selected playlist folder does not exist. Please choose a valid folder.",
                )
                return
            paths = glob.glob(os.path.join(source, "**", "_playlist_*.txt"), recursive=True)
            if not paths:
                self.logger.log("No playlist ledger files found in the selected folder.")
                return

        if not self.config_manager.is_valid():
            messagebox.showwarning(
                "Configuration Required",
                "Please complete config.json before attempting Spotify synchronization.",
            )
            return

        self.cancel_sync_event.clear()
        self._prepare_sync_ui(True)
        self.active_thread = threading.Thread(
            target=self._sync_worker,
            args=(paths,),
            daemon=True,
        )
        self.active_thread.start()

    def on_remove_duplicates_clicked(self) -> None:
        if self.active_thread and self.active_thread.is_alive():
            return
        if not self.config_manager.is_valid():
            messagebox.showwarning(
                "Configuration Required",
                "Please complete config.json before attempting Spotify operations.",
            )
            return

        service = SpotifyService(self.config_manager.data, self.logger, self.cache)
        if service.spotify is None or not service.user_id:
            messagebox.showerror(
                "Spotify Error",
                "Unable to connect to Spotify. Please check your settings and try again.",
            )
            return

        playlists = service.get_user_playlists()
        if not playlists:
            messagebox.showinfo(
                "No Playlists Found",
                "No Spotify playlists were found for your account.",
            )
            return

        dialog = PlaylistSelectionDialog(self.root, playlists)
        selected_playlist = dialog.selected_playlist
        if not selected_playlist:
            return

        self.active_thread = threading.Thread(
            target=self._remove_duplicates_worker,
            args=(service, selected_playlist),
            daemon=True,
        )
        self.active_thread.start()

    def _remove_duplicates_worker(self, service: SpotifyService, playlist: Dict[str, object]) -> None:
        playlist_id = playlist.get("id")
        playlist_name = playlist.get("name", "Unnamed Playlist")
        if not playlist_id:
            self.logger.log("Selected playlist does not have a valid Spotify ID.")
            return

        self.logger.log(f"Checking playlist '{playlist_name}' for duplicate tracks.")
        tracks = service.get_playlist_tracks(playlist_id)
        if not tracks:
            self._queue_ui_task(
                "notify",
                "No Tracks Found",
                f"Playlist '{playlist_name}' does not contain any tracks.",
            )
            return

        duplicates_by_uri: Dict[str, List[Dict[str, object]]] = {}
        for position, track in enumerate(tracks):
            uri = track.get("uri")
            if not uri:
                continue
            entries = duplicates_by_uri.setdefault(uri, [])
            entries.append(
                {
                    "position": position,
                    "name": track.get("name", "Unknown Track"),
                    "artist": ", ".join(
                        artist.get("name", "") for artist in track.get("artists", []) if artist
                    ),
                }
            )

        duplicate_items: List[Dict[str, object]] = []
        for uri, entries in duplicates_by_uri.items():
            if len(entries) > 1:
                duplicate_items.append(
                    {
                        "uri": uri,
                        "name": entries[0].get("name", "Unknown Track"),
                        "artist": entries[0].get("artist", "Unknown Artist"),
                        "count": len(entries),
                        "positions": [entry["position"] for entry in entries],
                    }
                )

        if not duplicate_items:
            self._queue_ui_task(
                "notify",
                "No Duplicate Tracks",
                f"No duplicate tracks were found in '{playlist_name}'.",
            )
            return

        selection_event = threading.Event()
        selection_result: Dict[str, object] = {}
        self.ui_tasks.put(("prompt_duplicate_resolver", playlist_name, duplicate_items, selection_event, selection_result))
        if not selection_event.wait(300):
            self.logger.log("Duplicate removal prompt timed out.")
            self._queue_ui_task(
                "notify",
                "Duplicate Removal Timeout",
                "Duplicate removal was cancelled because the prompt did not respond in time.",
            )
            return

        action = selection_result.get("action")
        if action not in ("keep_first", "keep_last"):
            self.logger.log(f"Duplicate removal cancelled for playlist '{playlist_name}'.")
            return

        tracks_to_remove: List[Dict[str, object]] = []
        for item in duplicate_items:
            positions = item.get("positions", [])
            if not positions:
                continue
            if action == "keep_last":
                remove_positions = positions[:-1]
            else:
                remove_positions = positions[1:]
            if remove_positions:
                tracks_to_remove.append({"uri": item["uri"], "positions": remove_positions})

        if not tracks_to_remove:
            self._queue_ui_task(
                "notify",
                "No Duplicate Tracks Removed",
                f"No duplicate track positions were selected for removal in '{playlist_name}'.",
            )
            return

        removed_count = sum(len(item["positions"]) for item in tracks_to_remove)
        confirm_event = threading.Event()
        confirm_result: Dict[str, object] = {}
        self.ui_tasks.put(("prompt_confirm_duplicate_removal", playlist_name, removed_count, confirm_event, confirm_result))
        if not confirm_event.wait(300):
            self.logger.log("Duplicate removal confirmation prompt timed out.")
            self._queue_ui_task(
                "notify",
                "Duplicate Removal Timeout",
                "Duplicate removal was cancelled because the confirmation prompt did not respond in time.",
            )
            return

        confirmed = confirm_result.get("confirmed")
        if not confirmed:
            self.logger.log(f"Duplicate removal cancelled by user for playlist '{playlist_name}'.")
            self._queue_ui_task(
                "notify",
                "Duplicate Removal Cancelled",
                f"No tracks were removed from '{playlist_name}'.",
            )
            return

        success = service.remove_playlist_items(playlist_id, tracks_to_remove)
        if success:
            self.logger.log(f"Removed {removed_count} duplicate track occurrences from playlist '{playlist_name}'.")
            self._queue_ui_task(
                "notify",
                "Duplicates Removed",
                f"Removed {removed_count} duplicate track occurrences from '{playlist_name}'.",
            )
        else:
            self.logger.log(f"Failed to remove duplicates from playlist '{playlist_name}'.")
            self._queue_ui_task(
                "notify",
                "Duplicate Removal Failed",
                f"Unable to remove duplicates from '{playlist_name}'.",
            )

    def on_cancel_sync(self) -> None:
        self.cancel_sync_event.set()
        self.logger.log("Sync cancellation requested.")

    def _prepare_scan_ui(self, running: bool) -> None:
        if running:
            self.generate_button.state(["disabled"])
            self.cancel_scan_button.state(["!disabled"])
            self.progress_bar.config(maximum=100, value=0)
            self.current_task_var.set("Scanning music folders...")
            self.progress_detail_var.set("0 / 0 files")
            self.progress_percent_var.set("0%")
            self.status_label.config(text="Scanning...")
        else:
            self.generate_button.state(["!disabled"])
            self.cancel_scan_button.state(["disabled"])
            self.progress_bar.config(value=0)
            self.current_task_var.set("Ready")
            self.progress_detail_var.set("")
            self.progress_percent_var.set("0%")
            self.status_label.config(text="Ready")

    def _prepare_sync_ui(self, running: bool) -> None:
        if running:
            self.sync_button.state(["disabled"])
            self.cancel_sync_button.state(["!disabled"])
            self.progress_bar.config(maximum=100, value=0)
            self.progress_percent_var.set("0%")
            self.current_task_var.set("Preparing Spotify sync...")
            self.progress_detail_var.set("")
            self.status_label.config(text="Synchronizing...")
        else:
            self.sync_button.state(["!disabled"])
            self.cancel_sync_button.state(["disabled"])
            self.progress_bar.config(value=0)
            self.current_task_var.set("Ready")
            self.progress_detail_var.set("")
            self.progress_percent_var.set("0%")
            self.status_label.config(text="Ready")

    def open_settings_window(self) -> None:
        if self.settings_window is not None and self.settings_window.winfo_exists():
            self.settings_window.focus_force()
            return

        win = tk.Toplevel(self.root)
        self.settings_window = win
        win.title("API Configuration")
        win.geometry("480x260")
        win.transient(self.root)
        win.protocol("WM_DELETE_WINDOW", self._close_settings_window)

        ttk.Label(win, text="Spotify Client ID:").grid(row=0, column=0, sticky="w", padx=8, pady=6)
        client_id_var = tk.StringVar(value=self.config_manager.data.get("SPOTIFY_CLIENT_ID", ""))
        client_id_entry = ttk.Entry(win, textvariable=client_id_var, width=60)
        client_id_entry.grid(row=0, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(win, text="Spotify Client Secret:").grid(row=1, column=0, sticky="w", padx=8, pady=6)
        client_secret_var = tk.StringVar(value=self.config_manager.data.get("SPOTIFY_CLIENT_SECRET", ""))
        client_secret_entry = ttk.Entry(win, textvariable=client_secret_var, width=60, show="*")
        client_secret_entry.grid(row=1, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(win, text="Redirect URI:").grid(row=2, column=0, sticky="w", padx=8, pady=6)
        redirect_var = tk.StringVar(value=self.config_manager.data.get("SPOTIFY_REDIRECT_URI", ""))
        redirect_entry = ttk.Entry(win, textvariable=redirect_var, width=60)
        redirect_entry.grid(row=2, column=1, sticky="w", padx=8, pady=6)

        ttk.Label(win, text="Default Music Directory:").grid(row=3, column=0, sticky="w", padx=8, pady=6)
        default_dir_var = tk.StringVar(value=self.config_manager.data.get("DEFAULT_MUSIC_DIRECTORY", ""))
        default_dir_entry = ttk.Entry(win, textvariable=default_dir_var, width=48)
        default_dir_entry.grid(row=3, column=1, sticky="w", padx=8, pady=6)

        def browse_dir():
            path = filedialog.askdirectory(title="Select Default Music Directory")
            if path:
                default_dir_var.set(path)

        browse_btn = ttk.Button(win, text="Browse", command=browse_dir)
        browse_btn.grid(row=3, column=2, sticky="w", padx=4)

        def save_settings():
            self.config_manager.data["SPOTIFY_CLIENT_ID"] = client_id_var.get().strip()
            self.config_manager.data["SPOTIFY_CLIENT_SECRET"] = client_secret_var.get().strip()
            self.config_manager.data["SPOTIFY_REDIRECT_URI"] = redirect_var.get().strip()
            self.config_manager.data["DEFAULT_MUSIC_DIRECTORY"] = default_dir_var.get().strip()
            self.config_manager.save()
            self._refresh_status_bar()
            win.destroy()

        save_btn = ttk.Button(win, text="Save Settings", command=save_settings)
        save_btn.grid(row=4, column=1, sticky="e", padx=8, pady=12)

    def _close_settings_window(self) -> None:
        if self.settings_window is not None:
            try:
                self.settings_window.destroy()
            except Exception:
                pass
            self.settings_window = None

    def _scan_worker(self, directory: str, extensions: List[str]) -> None:
        stats = ScanStatistics()
        playlist_lines_by_folder: Dict[str, List[str]] = {}
        seen_lines_by_folder: Dict[str, Set[str]] = {}
        try:
            audio_files = self._collect_audio_files(directory, extensions, stats)
            self._queue_ui_task(
                "update_progress",
                0,
                max(1, len(audio_files)),
                f"Scanning 0 / {len(audio_files)} files",
            )
            self.logger.log(f"Scanning {len(audio_files)} files for playlist generation.")

            current = 0
            for folder, file_name in audio_files:
                if self.cancel_scan_event.is_set():
                    self.logger.log("Scan canceled by user.")
                    break
                current += 1
                line = self._build_playlist_line(folder, file_name, stats)
                if line:
                    playlist_lines = playlist_lines_by_folder.setdefault(folder, [])
                    seen_lines = seen_lines_by_folder.setdefault(folder, set())
                    if line not in seen_lines:
                        playlist_lines.append(line)
                        seen_lines.add(line)
                self._queue_ui_task(
                    "update_progress",
                    current,
                    max(1, len(audio_files)),
                    f"Scanning {current} / {len(audio_files)} files",
                )

            for folder, lines in playlist_lines_by_folder.items():
                if self.cancel_scan_event.is_set():
                    break
                self._write_playlist_file(folder, lines)

            playlist_file_count = len(playlist_lines_by_folder)
            summary = (
                f"Scan complete. Folders scanned: {stats.folders_scanned}, "
                f"Tracks found: {stats.tracks_found}, Tagged tracks: {stats.tagged_tracks}, "
                f"Filename fallback tracks: {stats.fallback_tracks}. "
                f"Generated {playlist_file_count} playlist file(s)."
            )
            self.logger.log(summary)
            if playlist_file_count > 0:
                self.playlist_source_var.set(directory)
            self._queue_ui_task("update_summary", summary)
            self._queue_ui_task("refresh_status")
            if not self.cancel_scan_event.is_set():
                self._queue_ui_task("notify", "Scan Complete", summary)
        except Exception as error:
            self.logger.log(f"Unexpected scan error: {error}")
        finally:
            self.ui_tasks.put(("prepare_scan_ui", False))

    def _collect_audio_files(
        self, directory: str, extensions: List[str], stats: ScanStatistics
    ) -> List[Tuple[str, str]]:
        matching_files: List[Tuple[str, str]] = []
        for root, _, files in os.walk(directory):
            if self.cancel_scan_event.is_set():
                break
            found = [f for f in files if os.path.splitext(f)[1].lower() in extensions]
            if found:
                stats.folders_scanned += 1
                matching_files.extend((root, file_name) for file_name in found)
        stats.tracks_found = len(matching_files)
        return matching_files

    def _build_playlist_line(
        self, folder: str, file_name: str, stats: ScanStatistics
    ) -> Optional[str]:
        full_path = os.path.join(folder, file_name)
        ext = os.path.splitext(file_name)[1].lower()
        artist, title = MetadataExtractor.parse_audio_tags(full_path, ext, self.cancel_scan_event)
        if self.cancel_scan_event.is_set():
            return None

        if artist and title:
            stats.tagged_tracks += 1
            return f"{artist} - {title}"

        fallback_artist, fallback_title = MetadataExtractor.parse_audio_filename(file_name)
        if fallback_artist and fallback_title:
            stats.fallback_tracks += 1
            return f"{fallback_artist} - {fallback_title}"

        stats.fallback_tracks += 1
        return os.path.splitext(file_name)[0]

    def _write_playlist_file(self, folder: str, lines: List[str]) -> None:
        folder_name = os.path.basename(folder) or "Root_Directory"
        playlist_path = os.path.join(folder, f"_playlist_{folder_name}.txt")
        try:
            os.makedirs(folder, exist_ok=True)
            with open(playlist_path, "w", encoding="utf-8") as handle:
                handle.write("\n".join(lines))
                handle.write("\n")
        except Exception as error:
            self.logger.log(f"Could not write playlist ledger for '{folder_name}': {error}")

    def _sync_worker(self, playlist_paths: List[str]) -> None:
        if not self.config_manager.is_valid():
            self.logger.log("Spotify configuration is incomplete. Sync canceled.")
            self.ui_tasks.put(("prepare_sync_ui", False))
            return

        service = SpotifyService(self.config_manager.data, self.logger, self.cache)
        if service.spotify is None or not service.user_id:
            self.logger.log("Spotify service initialization failed. Sync canceled.")
            self.ui_tasks.put(("prepare_sync_ui", False))
            return

        unmatched_entries: List[str] = []
        total_playlists = len(playlist_paths)
        for index, playlist_path in enumerate(playlist_paths, start=1):
            if self.cancel_sync_event.is_set():
                self.logger.log("Sync canceled by user.")
                break
            self.logger.log(f"Processing playlist ledger {index}/{total_playlists}: {playlist_path}")
            playlist_stats, playlist_unmatched = self._process_playlist_file(
                playlist_path, service
            )
            unmatched_entries.extend(playlist_unmatched)
            self.logger.log(
                f"Playlist stats for '{os.path.basename(playlist_path)}': "
                f"Tracks processed: {playlist_stats.processed_tracks}, "
                f"Matches: {playlist_stats.matches}, "
                f"Cache hits: {playlist_stats.cache_hits}, "
                f"Failed matches: {playlist_stats.failed_matches}, "
                f"Duplicates removed: {playlist_stats.duplicates_removed}."
            )
            self._queue_ui_task(
                "update_progress",
                int(index / total_playlists * 100),
                100,
                f"Syncing playlists {index}/{total_playlists}",
            )

        if unmatched_entries:
            self._write_unmatched_report(unmatched_entries)
            self.logger.log(f"Generated unmatched report: {UNMATCHED_FILE}")

        self.cache.save()
        self._queue_ui_task("refresh_status")
        if not self.cancel_sync_event.is_set():
            summary = f"Sync complete. Playlists processed: {total_playlists}. See logs for details."
            self._queue_ui_task("notify", "Sync Complete", summary)
        self.ui_tasks.put(("prepare_sync_ui", False))

    def _process_playlist_file(
        self, playlist_path: str, service: SpotifyService
    ) -> Tuple[SyncStatistics, List[str]]:
        playlist_name = os.path.basename(playlist_path).replace("_playlist_", "").replace(".txt", "")
        queries: List[str] = []
        unmatched: List[str] = []
        stats = SyncStatistics()

        try:
            with open(playlist_path, "r", encoding="utf-8") as handle:
                queries = [line.strip() for line in handle if line.strip()]
        except Exception as error:
            self.logger.log(f"Could not read playlist file '{playlist_path}': {error}")
            return stats, [f"Playlist: {playlist_name}", f"Error reading playlist file: {error}"]

        if not queries:
            self.logger.log(f"Playlist file '{playlist_path}' is empty.")
            return stats, []

        self._queue_ui_task(
            "update_progress",
            0,
            max(1, len(queries)),
            "Searching Spotify...",
        )

        playlist_uri_list: List[str] = []
        playlist_uri_seen: Set[str] = set()
        for index, track_line in enumerate(queries, start=1):
            if self.cancel_sync_event.is_set():
                break
            stats.processed_tracks += 1
            uri, was_cached = service.search_track(track_line)
            if uri:
                if uri in playlist_uri_seen:
                    stats.duplicates_removed += 1
                else:
                    playlist_uri_seen.add(uri)
                    playlist_uri_list.append(uri)
                    stats.matches += 1
                    if was_cached:
                        stats.cache_hits += 1
            else:
                stats.failed_matches += 1
                unmatched.append(track_line)
            self._queue_ui_task(
                "update_progress",
                index,
                max(1, len(queries)),
                f"Searching Spotify... {index} / {len(queries)}",
            )

        if self.cancel_sync_event.is_set():
            return stats, unmatched

        if not playlist_uri_list:
            self.logger.log(f"No Spotify matches for playlist '{playlist_name}'.")
            return stats, [f"Playlist: {playlist_name}", "Not Found:", *unmatched]

        destination_event = threading.Event()
        destination_result: Dict[str, object] = {}
        self.ui_tasks.put(("prompt_import_destination", playlist_name, destination_event, destination_result))
        if not destination_event.wait(300):
            self.logger.log("UI prompt timeout while waiting for import destination.")
            return stats, ["Import cancelled due to timeout"]

        append_to_existing = False
        if destination_result.get("destination") == "existing":
            playlists = service.get_user_playlists()
            if not playlists:
                self.logger.log("No existing Spotify playlists found. Creating a new playlist instead.")
                self._queue_ui_task(
                    "notify",
                    "No Existing Playlists",
                    "No existing Spotify playlists were found. A new playlist will be created instead.",
                )
            else:
                selection_event = threading.Event()
                selection_result: Dict[str, object] = {}
                self.ui_tasks.put(("prompt_playlist_selection", playlists, selection_event, selection_result))
                if not selection_event.wait(300):
                    self.logger.log("UI prompt timeout while waiting for playlist selection.")
                    return stats, ["Import cancelled due to timeout"]
                selected_playlist = selection_result.get("selected_playlist")
                if not selected_playlist:
                    self.logger.log("Import cancelled by user.")
                    return stats, ["Import cancelled"]
                playlist_id = selected_playlist.get("id")
                chosen_name = selected_playlist.get("name", playlist_name)
                chosen_desc = selected_playlist.get("description", "")
                chosen_public = selected_playlist.get("public", False)
                playlist_url = selected_playlist.get("external_urls", {}).get("spotify", "")
                append_to_existing = True

        if not append_to_existing:
            details_event = threading.Event()
            details_result: Dict[str, object] = {}
            self.ui_tasks.put(("prompt_playlist_details", playlist_name, details_event, details_result))
            if not details_event.wait(300):
                self.logger.log("UI prompt timeout while waiting for playlist details.")
                return stats, ["Import cancelled due to timeout"]
            user_config = details_result.get("details") or {"name": playlist_name, "description": "Generated by Tag2Tune", "public": False}
            chosen_name = user_config["name"]
            chosen_desc = user_config["description"]
            chosen_public = user_config["public"]
            existing_playlist = service.find_playlist_by_name(chosen_name)
            playlist_id = None
            playlist_url = ""
            if existing_playlist:
                conflict_event = threading.Event()
                conflict_result: Dict[str, object] = {}
                self.ui_tasks.put(("prompt_existing_playlist_conflict", chosen_name, conflict_event, conflict_result))
                if not conflict_event.wait(300):
                    self.logger.log("UI prompt timeout while waiting for existing playlist conflict.")
                    return stats, ["Import cancelled due to timeout"]
                action = conflict_result.get("action", "cancel")
                if action == "add":
                    playlist_id = existing_playlist.get("id")
                    chosen_name = existing_playlist.get("name", chosen_name)
                    chosen_desc = existing_playlist.get("description", "")
                    chosen_public = existing_playlist.get("public", False)
                    playlist_url = existing_playlist.get("external_urls", {}).get("spotify", "")
                    append_to_existing = True
                elif action == "rename":
                    rename_event = threading.Event()
                    rename_result: Dict[str, object] = {}
                    rename_name = f"{chosen_name} (Copy)"
                    self.ui_tasks.put(("prompt_playlist_details", rename_name, rename_event, rename_result))
                    if not rename_event.wait(300):
                        self.logger.log("UI prompt timeout while waiting for rename dialog.")
                        return stats, ["Import cancelled due to timeout"]
                    user_config = rename_result.get("details") or {"name": rename_name, "description": chosen_desc, "public": chosen_public}
                    chosen_name = user_config["name"]
                    chosen_desc = user_config["description"]
                    chosen_public = user_config["public"]
                    existing_playlist = service.find_playlist_by_name(chosen_name)
                    try:
                        playlist = service.spotify.user_playlist_create(
                            user=service.user_id,
                            name=chosen_name,
                            public=chosen_public,
                            description=chosen_desc,
                        )
                        playlist_id = playlist.get("id")
                        playlist_url = playlist.get("external_urls", {}).get("spotify", "")
                        service.playlist_cache[chosen_name.lower()] = playlist
                    except Exception as error:
                        self.logger.log(f"Failed to create playlist '{chosen_name}': {error}")
                        return stats, [f"Playlist: {chosen_name}", "Not Found:", *unmatched]
                else:
                    self.logger.log(f"Import cancelled by user for playlist '{chosen_name}'.")
                    return stats, ["Import cancelled"]
            else:
                try:
                    playlist = service.spotify.user_playlist_create(
                        user=service.user_id,
                        name=chosen_name,
                        public=chosen_public,
                        description=chosen_desc,
                    )
                    playlist_id = playlist.get("id")
                    playlist_url = playlist.get("external_urls", {}).get("spotify", "")
                    service.playlist_cache[chosen_name.lower()] = playlist
                except Exception as error:
                    self.logger.log(f"Failed to create playlist '{chosen_name}': {error}")
                    return stats, [f"Playlist: {chosen_name}", "Not Found:", *unmatched]

        self.logger.log(
            f"Uploading playlist '{chosen_name}' ({len(playlist_uri_list)} tracks)."
        )
        uris = playlist_uri_list
        total_uris = len(uris)
        self._queue_ui_task(
            "update_progress",
            0,
            max(1, total_uris),
            "Uploading playlist...",
        )

        def upload_progress(uploaded: int, total: int) -> None:
            self._queue_ui_task(
                "update_progress",
                uploaded,
                max(1, total),
                f"Uploading playlist... {uploaded} / {total}",
            )

        success = service.add_playlist_items(playlist_id, uris, progress_callback=upload_progress)

        if not success:
            self.logger.log(f"Failed to upload playlist '{chosen_name}'.")
            return stats, [f"Playlist: {chosen_name}", "Could not upload tracks."]

        self.logger.log(
            f"Playlist '{chosen_name}' uploaded successfully with {len(uris)} unique tracks."
        )
        if playlist_url:
            self.logger.log(f"🔗 Spotify Playlist Link: {playlist_url}")
            self._queue_ui_task("show_playlist_url", chosen_name, playlist_url)

        return stats, [f"Playlist: {chosen_name}", "Not Found:", *unmatched] if unmatched else []

    def _ask_update_existing(self, playlist_name: str) -> bool:
        event = threading.Event()
        result: Dict[str, bool] = {}
        self.ui_tasks.put(("prompt_playlist_update", playlist_name, event, result))
        event.wait()
        return result.get("answer", True)

    def _write_unmatched_report(self, entries: List[str]) -> None:
        try:
            with open(UNMATCHED_FILE, "w", encoding="utf-8") as handle:
                handle.write("\n".join(entries))
        except Exception as error:
            self.logger.log(f"Failed to write unmatched report: {error}")


if __name__ == "__main__":
    root = tk.Tk()
    app = Tag2TuneApp(root)
    root.mainloop()