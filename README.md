# Spotify to YouTube Music Playlist Transfer

A simple Python script to transfer your Spotify playlists to YouTube Music.

## Description

This tool allows you to easily transfer your Spotify playlists to YouTube Music. It fetches all tracks from a Spotify playlist and creates a new playlist with the same tracks on YouTube Music.

## Features

- Transfer complete Spotify playlists to YouTube Music
- Handles large playlists with pagination
- Preserves playlist name and description
- Simple command-line interface

## Prerequisites

- Python 3.6 or higher
- Spotify Developer account and API credentials
- YouTube Music/Google OAuth credentials

## Installation

1. Clone the repository:
   ```
   git clone https://github.com/yourusername/spotify-to-ytmusic.git
   cd spotify-to-ytmusic
   ```

2. Install required packages:
   ```
   pip install -r requirements.txt
   ```

3. Set up environment variables:
   - Copy `.env.example` to `.env`
   - Fill in your API credentials:
     - `S_ID`: Spotify Client ID
     - `S_SECRET`: Spotify Client Secret
     - `Y_ID`: YouTube/Google Client ID
     - `Y_SECRET`: YouTube/Google Client Secret

## Setting Up API Access

### Spotify API
1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/)
2. Create a new application
3. Note your Client ID and Client Secret
4. Add these to your `.env` file

### YouTube Music API
1. Set up a project in the [Google Cloud Console](https://console.cloud.google.com/)
2. Create OAuth credentials (TV client type)
3. Download the credentials and use them with the ytmusicapi setup
4. Set up OAuth:
   ```
   python -m ytmusicapi oauth
   ```
   This will create the `oauth.json` file needed for authentication.

## Usage

1. Run the script:
   ```
   python main.py
   ```

2. When prompted, enter the Spotify playlist link.
   Example: `https://open.spotify.com/playlist/37i9dQZEVXcJZyENOWUFo7`

3. The script will:
   - Fetch all tracks from the Spotify playlist
   - Create a new playlist on YouTube Music with the same name and description
   - Search for each track on YouTube Music and add it to the new playlist
   - Display progress and results in the terminal
