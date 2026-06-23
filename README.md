# Tag2Tune 🎵

[![Python Version](https://img.shields.io/badge/python-3.10%2B-green.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Theme: Spotify Dark](https://img.shields.io/badge/UI-Spotify%20Dark-1DB954.svg)](https://github.com/yourusername/Tag2Tune)

Convert decades of carefully organized local music folders into Spotify playlists automatically.

**Tag2Tune** is a lightweight Python desktop application that bridges the gap between legacy offline music libraries and modern cloud streaming platforms. Instead of manually searching and adding thousands of songs one by one, Tag2Tune scans your music directories, extracts track metadata, builds local ledger files, and recreates those exact collections inside your Spotify account with precision.

---

## 🔥 Key Features

- **Local Metadata Extraction:** Recursively scans directories reading ID3/Vorbis/FLAC tags (via Mutagen) with a clean fallback to filename parsing.
- **Sleek Spotify-Dark UI:** Minimalist desktop interface styled after Spotify’s native palette (`#121212` black with `#1DB954` green accents).
- **Existing Playlist Merging:** Import local ledger tracks into existing Spotify playlists instead of creating new ones.
- **✨ Smart Duplicate Remover Tool:** Scan playlists, compare Track/Artist/Album metadata, and choose which duplicates to keep.
- **Detailed Accountability Logs:** Full audit logs for scanned, matched, unmatched, and deleted tracks.

---

## 🛠️ How It Works

### Stage 1 — Local Extraction

Tag2Tune recursively scans your music directory. For supported formats (MP3, FLAC, WAV, M4A, AAC, OGG, AIFF):

1. Extracts artist and title metadata
2. Generates a local `_playlist_FolderName.txt` ledger file

### Stage 2 — Smart Matching & Cloud Sync

Uses the Spotify API to search and match tracks, handles pagination, and either:

- Creates a new playlist, or
- Appends to an existing playlist safely

---

## 🚀 Getting Started

### Requirements

- Python 3.10+
- Spotify account
- Spotify Developer App credentials

### Installation

```bash
git clone https://github.com/yourusername/Tag2Tune.git
cd Tag2Tune
```

````

```bash
pip install spotipy mutagen
```

(Optional UI enhancement)

```bash
pip install ttkbootstrap
```

````

## 🔑 Spotify API Configuration

1. Go to the Spotify Developer Dashboard: [https://developer.spotify.com/dashboard](https://developer.spotify.com/dashboard)
2. Create an application
3. Add Redirect URI:

```
http://localhost:8888/callback
```

4. Save and copy your:

- Client ID
- Client Secret
- Redirect URI

The app will prompt you for these in the UI securely.

---

## 📊 Typical Performance Metrics

- Tagged libraries (ID3 metadata): **95% - 99% match accuracy**
- Filename-only libraries: **80% - 95% match accuracy**

Accuracy improves with cleaner metadata.

---

## 🗺️ Roadmap & Ecosystem

### Completed (v2 & v3)

- [x] Spotify Dark UI Theme
- [x] Playlist selector interface
- [x] Multi-format audio support
- [x] Duplicate resolver tool
- [x] Unmatched track reporting
- [x] Secure OAuth token caching

### Upcoming

- Last.fm history import
- Improved cloud matching algorithm
- Fully async scanning pipeline

---

## 📄 License

This project is licensed under the MIT License. See `LICENSE` for details.
