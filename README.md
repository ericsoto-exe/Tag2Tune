# Tag2Tune

[![Python Version](https://img.shields.io/badge/python-3.10%2B-green.svg)](https://www.python.org/)
[![Theme: Spotify Dark](https://img.shields.io/badge/UI-Spotify%20Dark-1DB954.svg)](#)

Tag2Tune is a Python/Tkinter desktop app that turns a local music library into Spotify playlists. It scans folders, extracts track information from audio metadata or filenames, writes local playlist ledger files, and imports those ledgers into Spotify with progress tracking, logging, cache support, and duplicate cleanup tools.

The current interface uses a modern Spotify-inspired dark theme with dashboard cards, large action buttons, side-by-side workflow panels, a progress card, and a collapsible activity log.

## Features

- Modern Spotify-style desktop UI with dark cards, green primary actions, toolbar shortcuts, status dashboard, progress percentage, and developer-style activity log.
- Local library scanning for MP3, FLAC, WAV, M4A/ALAC, AAC, OGG, AIFF, WMA, and APE files.
- Format filtering so a scan can target all supported formats or one specific audio type.
- Metadata extraction through Mutagen, with filename parsing fallback for files without usable tags.
- Playlist ledger generation as `_playlist_<FolderName>.txt` files inside each scanned music folder.
- Spotify OAuth authentication through Spotipy with token caching in `.spotify_oauth_cache`.
- Spotify track search using `artist:<artist> track:<title>` queries when possible, with fallback searches.
- Spotify search cache in `spotify_cache.json` to reduce repeated API lookups.
- Import from either a whole playlist folder or a single playlist text file.
- New playlist creation with editable playlist name, description, and public/private setting.
- Import into an existing Spotify playlist selected from your account.
- Existing-name conflict handling: add to the existing playlist, create a renamed copy, or cancel.
- Duplicate URI filtering during import so the same matched track is uploaded only once per generated playlist.
- Dedicated duplicate-removal tool for existing Spotify playlists, with keep-first or keep-last choices.
- Unmatched track report written to `unmatched_tracks.txt`.
- Daily log files in the `logs/` folder.
- Cancellable scan and sync operations.
- Settings window for Spotify credentials, redirect URI, and default music directory.
- Clear Cache and Open Logs tools from the menu.

## Requirements

- Python 3.10 or newer
- A Spotify account
- A Spotify Developer app
- Python packages from `requirements.txt`

Install dependencies:

```bash
pip install -r requirements.txt
```

The runtime dependencies used by the app are:

- `spotipy`
- `mutagen`

The remaining packages in `requirements.txt` are helper, testing, or quality tools.

## Spotify Setup

1. Open the Spotify Developer Dashboard:
   [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create an app.
3. Add this redirect URI unless you intentionally use another one:

```text
http://127.0.0.1:8888/callback
```

4. Copy the Client ID and Client Secret.
5. Open Tag2Tune.
6. Go to `Tools -> Spotify Settings`.
7. Enter:
   - Spotify Client ID
   - Spotify Client Secret
   - Redirect URI
   - Default Music Directory, optional
8. Click `Save Settings`.

The settings are stored in `config.json`.

## Running the App

From the project folder:

```bash
python Tag2Tune.py
```

On first Spotify use, the app may ask you to authorize in your browser and paste the redirected URL back into the prompt. The OAuth token is cached in `.spotify_oauth_cache`.

## Main Workflow

### 1. Generate Playlist Files

Use this when you want Tag2Tune to scan your local music folders and create local playlist ledger files.

1. In the `Music Library` card, click `Browse`.
2. Select the main folder that contains your music.
3. Choose a format filter:
   - `All Audio Formats`
   - `MP3 (*.mp3)`
   - `FLAC Lossless (*.flac)`
   - `WAV Hi-Res (*.wav)`
   - `M4A/ALAC (*.m4a)`
   - `AAC (*.aac)`
   - `OGG (*.ogg)`
   - `AIFF (*.aiff)`
   - `WMA (*.wma)`
   - `APE (*.ape)`
4. Click `Generate`.
5. Watch the progress card and activity log.

For every folder that contains matching audio files, Tag2Tune writes a file named like:

```text
_playlist_FolderName.txt
```

Each line is a track query such as:

```text
Artist - Title
```

If metadata is missing, Tag2Tune tries filename patterns such as:

```text
Artist - Title
Artist_Title
Artist | Title
01 - Artist - Title
```

If a scan is running, click `Cancel` in the Music Library card to request cancellation.

### 2. Import Playlists into Spotify

Use this when you already have generated `_playlist_*.txt` files and want to create or update Spotify playlists.

1. In the `Spotify Import` card, choose the import mode:
   - `Playlist Folder`
   - `Single Playlist File`
2. Click `Browse`.
3. Select either:
   - A folder containing generated `_playlist_*.txt` files, or
   - One generated playlist text file.
4. Leave `Update Existing` enabled if you want existing playlist update behavior available during import.
5. Click `Import`.
6. Follow the prompts.

During import, Tag2Tune searches Spotify for each ledger line, caches successful matches, removes duplicate matched URIs inside the upload list, and uploads tracks in batches.

If syncing is running, click `Cancel` in the Spotify Import card to request cancellation.

## Import Prompts

When importing a playlist file, Tag2Tune can ask how you want to handle the destination.

### Choose Destination

You can import into:

- A new Spotify playlist
- An existing Spotify playlist

### New Playlist Details

For a new playlist, you can set:

- Playlist name
- Description
- Public or private visibility

### Existing Playlist Selection

If you choose an existing playlist, Tag2Tune loads your Spotify playlists and shows a searchable selector.

### Name Conflict Handling

If you create a playlist with a name that already exists, Tag2Tune lets you choose:

- Add tracks to the existing playlist
- Create a renamed copy
- Cancel the import

## Duplicate Removal Tool

Use this when you want to clean repeated tracks from a Spotify playlist.

1. Make sure Spotify credentials are configured.
2. Go to `Tools -> Remove Duplicates from Playlist`.
3. Select a Spotify playlist.
4. If duplicates are found, choose one option:
   - Keep the first occurrence
   - Keep the last occurrence
5. Confirm the removal.

The tool compares duplicate Spotify track URIs and removes specific duplicate occurrences from the playlist.

## Toolbar

The top toolbar provides quick access to the most common actions:

- `Generate Playlist`: starts local playlist ledger generation.
- `Import into Spotify`: starts Spotify import for the selected source.
- `Open Logs`: opens the `logs/` folder.
- `Settings`: opens Spotify and default-directory settings.

## Dashboard and Status

The dashboard cards show:

- Spotify connection/configuration status
- Number of cached Spotify track lookups
- Selected library or latest scan summary

The progress card shows:

- Current task
- Progress bar
- Percentage
- Current count or detail text

The bottom status text reports whether the app is ready, scanning, syncing, or missing Spotify credentials.

## Activity Log

The activity log is shown at the bottom of the app. It mirrors runtime messages and is useful for checking scan and sync details.

- Click `Hide Activity Log` to collapse it.
- Click `Show Activity Log` to restore it.
- Use `Open Logs` or `Tools -> Open Log Folder` to open daily log files stored in `logs/`.

## Cache

Tag2Tune stores successful Spotify track searches in:

```text
spotify_cache.json
```

This makes repeated imports faster and reduces duplicate API lookups.

To clear cached track lookups:

1. Open the `Tools` menu.
2. Click `Clear Cache`.

The cache is safe to rebuild. Clearing it does not delete playlists or configuration.

## Output Files

Tag2Tune creates and uses these local files:

- `config.json`: Spotify credentials, redirect URI, and default music directory.
- `spotify_cache.json`: cached Spotify track search results.
- `.spotify_oauth_cache`: cached Spotify OAuth token.
- `logs/YYYY-MM-DD.log`: daily app logs.
- `unmatched_tracks.txt`: report of tracks that could not be matched on Spotify.
- `_playlist_<FolderName>.txt`: generated playlist ledger files inside scanned music folders.

## Supported Audio Formats

| UI option | Extensions |
| --- | --- |
| All Audio Formats | `.mp3`, `.flac`, `.wav`, `.m4a`, `.aac`, `.ogg`, `.wma`, `.aiff`, `.ape` |
| MP3 | `.mp3` |
| FLAC Lossless | `.flac` |
| WAV Hi-Res | `.wav` |
| M4A/ALAC | `.m4a` |
| AAC | `.aac` |
| OGG | `.ogg` |
| AIFF | `.aiff` |
| WMA | `.wma` |
| APE | `.ape` |

## Troubleshooting

### Spotify status says Not Configured

Open `Tools -> Spotify Settings` and confirm that Client ID, Client Secret, and Redirect URI are filled in.

### No playlist files are found during import

Use `Generate` first, or select the folder that contains files named `_playlist_*.txt`.

### Tracks are missing after import

Check:

- `unmatched_tracks.txt`
- The daily log in `logs/`
- The spelling and metadata of the source files

Spotify matching is only as good as the available metadata or filename text.

### Authentication fails

Confirm that the redirect URI in Spotify Developer Dashboard exactly matches the redirect URI in Tag2Tune settings.

### The app opens but sync is disabled

Sync is disabled until Spotify credentials are configured.

## Development Notes

The main application file is:

```text
Tag2Tune.py
```

The app uses:

- Tkinter and ttk for the desktop interface
- Mutagen for metadata extraction
- Spotipy for Spotify API access
- Background threads and UI queues for long-running scan/sync operations

The UI is organized into helper methods such as:

- `_configure_styles`
- `_build_header`
- `_build_dashboard`
- `_build_toolbar`
- `_build_music_card`
- `_build_import_card`
- `_build_progress_card`
- `_build_console`
- `_build_statusbar`

## License

This project is licensed under the MIT License.
