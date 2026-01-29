# SpotTransfer - Spotify to YouTube Music Playlist Transfer

A Flask web application that transfers your Spotify playlists to YouTube Music.

## Features

- OAuth 2.0 authentication for YouTube Music
- Redis caching for playlist data and search results
- Handles large playlists with pagination
- Preserves playlist name and description
- Track-by-track transfer with status updates
- YouTube API quota management and error handling
- Lists tracks that could not be found on YouTube Music

## Prerequisites

- Python 3.6 or higher
- Redis server
- Spotify Developer account and API credentials
- Google Cloud project with YouTube Data API v3 enabled
- OAuth 2.0 credentials for YouTube

## Installation

1. Clone the repository:
   ```bash
   git clone https://github.com/yourusername/spottransfer.git
   cd spottransfer
   ```

2. Install required packages:
   ```bash
   pip install -r requirements.txt
   ```

3. Set up Redis:
   - Install Redis on your system
   - Start the Redis server:
     ```bash
     redis-server
     ```

4. Configure environment variables:
   - Copy `.env.example` to `.env`
   - Fill in your API credentials (see Configuration section below)

## Configuration

Create a `.env` file with the following variables:

```
SPOTIFY_CLIENT_ID=your_spotify_client_id
SPOTIFY_CLIENT_SECRET=your_spotify_client_secret
GOOGLE_CLIENT_ID=your_google_client_id
GOOGLE_CLIENT_SECRET=your_google_client_secret
REDIRECT_URI=http://localhost:5000/oauth2callback
FLASK_SECRET_KEY=your_random_secret_key
FLASK_DEVELOPMENT=FALSE
REDIS_HOST=localhost
REDIS_PORT=6379
REDIS_DB=0
REDIS_PASSWORD=
```

### Environment Variables Explained

- `SPOTIFY_CLIENT_ID`: Your Spotify application client ID
- `SPOTIFY_CLIENT_SECRET`: Your Spotify application client secret
- `GOOGLE_CLIENT_ID`: Your Google OAuth 2.0 client ID
- `GOOGLE_CLIENT_SECRET`: Your Google OAuth 2.0 client secret
- `REDIRECT_URI`: OAuth callback URL (must match Google Cloud Console configuration)
- `FLASK_SECRET_KEY`: Random secret key for Flask sessions (generate a strong random string)
- `FLASK_DEVELOPMENT`: Set to TRUE for local development, FALSE for production
- `REDIS_HOST`: Redis server hostname
- `REDIS_PORT`: Redis server port
- `REDIS_DB`: Redis database number
- `REDIS_PASSWORD`: Redis password (leave empty if not configured)

## Setting Up API Access

### Spotify API

1. Go to the [Spotify Developer Dashboard](https://developer.spotify.com/dashboard/)
2. Create a new application
3. Note your Client ID and Client Secret
4. Add these credentials to your `.env` file

### YouTube Data API

1. Create a project in the [Google Cloud Console](https://console.cloud.google.com/)
2. Enable the YouTube Data API v3
3. Create OAuth 2.0 credentials:
   - Application type: Web application
   - Authorized redirect URIs: `http://localhost:5000/oauth2callback`
4. Download the credentials and add the Client ID and Client Secret to your `.env` file

Note: The YouTube Data API has daily quota limits. The application will notify you if the quota is exceeded.

## Usage

1. Start the Flask application:
   ```bash
   python app.py
   ```

2. Open your browser and navigate to:
   ```
   http://localhost:5000
   ```

3. Connect your YouTube account:
   - Click "Connect YouTube Account"
   - Authorize the application in the popup window
   - The popup will close automatically after authorization

4. Transfer a playlist:
   - Paste your Spotify playlist URL in the input field
   - Example: `https://open.spotify.com/playlist/37i9dQZEVXcJZyENOWUFo7`
   - Click "Start Transfer"
   - Monitor the real-time progress as tracks are transferred

5. The application will:
   - Fetch all tracks from the Spotify playlist
   - Create a new private playlist on YouTube Music
   - Search for each track on YouTube Music
   - Add found tracks to the playlist
   - Display a list of tracks that could not be found

## Project Structure

```
spottransfer/
├── app.py                 # Main Flask application
├── requirements.txt       # Python dependencies
├── .env.example          # Environment variables template
├── .gitignore            # Git ignore rules
├── static/
│   ├── script.js         # Frontend JavaScript
│   └── style.css         # Application styles
└── templates/
    └── index.html        # Main HTML template
```

## License

This project is open source and available under the MIT License.

## Contributing

Contributions are welcome! Please feel free to submit a Pull Request.
