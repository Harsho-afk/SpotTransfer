from flask import (
    Flask,
    render_template,
    request,
    redirect,
    url_for,
    session,
    jsonify,
    make_response,
)
from flask_session import Session
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
import os
import re
import json
import redis
import time
from dotenv import load_dotenv
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()
os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"
app = Flask(__name__)

# Session configuration
app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
    SESSION_TYPE="redis",
    SESSION_PERMANENT=False,
    SESSION_USE_SIGNER=True,
    SESSION_KEY_PREFIX="spottransfer:session:",
)

# Redis key prefixes
OAUTH_STATE_PREFIX = "spottransfer:oauth_state:"
OAUTH_CREDS_PREFIX = "spottransfer:oauth_creds:"
SPOTIFY_CACHE_PREFIX = "spottransfer:spotify:"

# TTL settings (in seconds)
OAUTH_STATE_TTL = 600  # 10 minutes
OAUTH_CREDS_TTL = 300  # 5 minutes
SPOTIFY_CACHE_TTL = 3600  # 1 hour

# Google OAuth scopes
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]


def create_redis_client(decode_responses=True):
    """Create a Redis client with common configuration"""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        db=int(os.environ.get("REDIS_DB", 0)),
        password=os.environ.get("REDIS_PASSWORD", None),
        decode_responses=decode_responses,
    )


# Client for session storage (binary data)
redis_session_client = create_redis_client(decode_responses=False)

# Client for general use (string data)
redis_client = create_redis_client(decode_responses=True)

# Configure session with Redis
app.config["SESSION_REDIS"] = redis_session_client
Session(app)


def get_ytclient_config():
    """Get Google OAuth client configuration"""
    return {
        "web": {
            "client_id": os.environ.get("GOOGLE_CLIENT_ID", ""),
            "client_secret": os.environ.get("GOOGLE_CLIENT_SECRET", ""),
            "auth_uri": "https://accounts.google.com/o/oauth2/auth",
            "token_uri": "https://oauth2.googleapis.com/token",
            "redirect_uris": [
                os.environ.get("REDIRECT_URI", "http://localhost:5000/oauth2callback")
            ],
        }
    }


def get_spotify_client():
    """Initialize Spotify client with credentials"""
    client_id = os.environ.get("SPOTIFY_CLIENT_ID")
    client_secret = os.environ.get("SPOTIFY_CLIENT_SECRET")

    if not client_id or not client_secret:
        return None

    auth_manager = SpotifyClientCredentials(
        client_id=client_id, client_secret=client_secret
    )
    return spotipy.Spotify(auth_manager=auth_manager)


def extract_playlist_id(url):
    """Extract playlist ID from Spotify URL"""
    match = re.search(r"playlist/([a-zA-Z0-9]+)", url)
    return match.group(1) if match else None


def get_cached_playlist(playlist_id):
    """Get cached playlist data from Redis"""
    try:
        cache_key = f"{SPOTIFY_CACHE_PREFIX}playlist:{playlist_id}"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            redis_client.expire(cache_key, SPOTIFY_CACHE_TTL)
            return json.loads(cached_data)
        return None
    except Exception as e:
        print(f"DEBUG: Cache error: {e}")
        return None


def cache_playlist(playlist_id, data):
    """Cache playlist data in Redis"""
    try:
        cache_key = f"{SPOTIFY_CACHE_PREFIX}playlist:{playlist_id}"
        redis_client.setex(cache_key, SPOTIFY_CACHE_TTL, json.dumps(data))
    except Exception as e:
        print(f"DEBUG: Cache write error: {e}")


def fetch_spotify_playlist(spotify_client, playlist_id):
    """Fetch playlist data from Spotify API"""
    playlist_info = spotify_client.playlist(playlist_id)
    playlist_name = playlist_info["name"]
    playlist_desc = playlist_info.get("description", "")
    tracks = []
    results = spotify_client.playlist_tracks(playlist_id)
    tracks.extend(results["items"])

    while results["next"]:
        results = spotify_client.next(results)
        tracks.extend(results["items"])

    if not tracks:
        return None, None, []

    track_names = []
    for track in tracks:
        if track["track"] and track["track"]["name"]:
            artists = ", ".join(artist["name"] for artist in track["track"]["artists"])
            track_names.append(f"{track['track']['name']} - {artists}")

    print(track_names)

    return playlist_name, playlist_desc, track_names


def search_youtube_music(youtube, query):
    """Search for a song on YouTube Music"""
    try:
        cache_key = f"{SPOTIFY_CACHE_PREFIX}search:{query}"
        cached_result = redis_client.get(cache_key)
        if cached_result:
            redis_client.expire(cache_key, SPOTIFY_CACHE_TTL)
            return cached_result

        search_response = (
            youtube.search()
            .list(
                q=query,
                part="id,snippet",
                maxResults=5,
                type="video",
                videoCategoryId="10",  # Music category
            )
            .execute()
        )

        if search_response.get("items"):
            video_id = search_response["items"][0]["id"]["videoId"]
            redis_client.setex(cache_key, SPOTIFY_CACHE_TTL, video_id)
            print(f"DEBUG: Cached search result for: {query}")
            return video_id

        return None

    except Exception as e:
        error_msg = str(e)
        if "quota" in error_msg.lower():
            raise Exception("QUOTA_EXCEEDED")
        print(f"DEBUG: YouTube search error: {e}")
        return None


def create_youtube_playlist(youtube, title, description):
    """Create a new playlist on YouTube"""
    try:
        playlist = (
            youtube.playlists()
            .insert(
                part="snippet,status",
                body={
                    "snippet": {
                        "title": title,
                        "description": description[:5000] if description else "",
                    },
                    "status": {"privacyStatus": "private"},
                },
            )
            .execute()
        )
        return playlist["id"]
    except Exception as e:
        print(f"DEBUG: Error creating YouTube playlist: {e}")
        return None


def add_to_youtube_playlist(youtube, playlist_id, video_id, max_retries=3):
    """Add a video to YouTube playlist with retry logic"""
    for attempt in range(max_retries):
        try:
            youtube.playlistItems().insert(
                part="snippet",
                body={
                    "snippet": {
                        "playlistId": playlist_id,
                        "resourceId": {"kind": "youtube#video", "videoId": video_id},
                    }
                },
            ).execute()
            return True

        except HttpError as e:
            error_details = str(e)
            status_code = e.resp.status

            if status_code == 409:
                # video might already be in playlist or temporary issue
                if "duplicate" in error_details.lower():
                    return True

                # SERVICE_UNAVAILABLE: retry with backoff
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    time.sleep(wait_time)
                    continue
                else:
                    return False

            elif status_code == 403 and "quota" in error_details.lower():
                raise Exception("QUOTA_EXCEEDED")

            elif status_code >= 500:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    time.sleep(wait_time)
                    continue
                else:
                    return False
            else:
                print(
                    f"DEBUG: Error adding video to playlist (status {status_code}): {e}"
                )
                return False

        except Exception as e:
            print(f"DEBUG: Unexpected error adding video to playlist: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return False

    return False


def cleanup_old_oauth_states(session_id):
    """Remove any existing OAuth states for a session"""
    try:
        pattern = f"{OAUTH_STATE_PREFIX}*"
        for key in redis_client.scan_iter(match=pattern):
            stored_session_id = redis_client.get(key)
            if stored_session_id == session_id:
                old_state = key.replace(OAUTH_STATE_PREFIX, "")
                redis_client.delete(key)
                redis_client.delete(f"{OAUTH_CREDS_PREFIX}{old_state}")
    except Exception as e:
        print(f"DEBUG: Error cleaning up old states: {e}")


@app.errorhandler(Exception)
def handle_redis_decode_error(e):
    """Handle Redis decode errors by clearing the session"""
    if "UnicodeDecodeError" in str(type(e)) or "decode" in str(e).lower():
        # For JSON endpoints, return JSON error
        if request.path.startswith(("/complete_auth", "/transfer", "/cache_stats")):
            return (
                jsonify(
                    {
                        "error": "Session corrupted. Please clear your browser cookies and try again."
                    }
                ),
                500,
            )
        response = make_response(redirect(url_for("index")))
        response.set_cookie("spottransfer_session", "", expires=0)
        return response
    raise e


@app.route("/")
def index():
    """Main page"""
    youtube_authenticated = "credentials" in session
    return render_template("index.html", youtube_authenticated=youtube_authenticated)


@app.route("/authorize")
def authorize():
    """Start OAuth flow"""
    client_config = get_ytclient_config()
    if (
        not client_config["web"]["client_id"]
        or not client_config["web"]["client_secret"]
    ):
        return "OAuth credentials not configured", 400

    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        redirect_uri=client_config["web"]["redirect_uris"][0],
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline", include_granted_scopes="true", prompt="consent"
    )

    session["oauth_initiated"] = True
    session_id = request.cookies.get("session")

    if not session_id:
        session_id = session.sid if hasattr(session, "sid") else str(id(session))

    # Clean up old failed OAuth states for this session
    cleanup_old_oauth_states(session_id)

    state_key = f"{OAUTH_STATE_PREFIX}{state}"
    redis_client.setex(state_key, OAUTH_STATE_TTL, session_id)

    return redirect(authorization_url)


@app.route("/oauth2callback")
def oauth2callback():
    """Handle OAuth callback from Google"""
    state_from_google = request.args.get("state")
    state_key = f"{OAUTH_STATE_PREFIX}{state_from_google}"
    original_session_id = redis_client.get(state_key)

    if not original_session_id:
        return (
            """
        <html>
        <body>
            <h2>Authentication Timeout</h2>
            <p>Your authentication session expired (10 minute limit).</p>
            <p>Please close this window and try connecting again.</p>
            <script>setTimeout(() => window.close(), 3000);</script>
        </body>
        </html>
        """,
            400,
        )

    # Exchange authorization code for credentials
    client_config = get_ytclient_config()
    flow = Flow.from_client_config(
        client_config,
        scopes=SCOPES,
        state=state_from_google,
        redirect_uri=client_config["web"]["redirect_uris"][0],
    )

    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    credentials_dict = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }

    # Store credentials temporarily in Redis
    creds_key = f"{OAUTH_CREDS_PREFIX}{state_from_google}"
    redis_client.setex(creds_key, OAUTH_CREDS_TTL, json.dumps(credentials_dict))

    return f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>Authentication Successful</title>
    </head>
    <body>
        <script>
            if (window.opener) {{
                window.opener.postMessage({{type: 'auth_complete', state: '{state_from_google}'}}, '*');
            }}
            window.close();
            setTimeout(() => window.location.href = '/', 100);
        </script>
        <p>Authentication successful! You can close this window.</p>
    </body>
    </html>
    """


@app.route("/complete_auth", methods=["POST"])
def complete_auth():
    """Complete authentication by storing credentials in user's session"""
    data = request.json
    state = data.get("state")

    print(f"DEBUG: complete_auth called with state: {state}")

    if not state:
        return jsonify({"error": "No state provided"}), 400

    # Retrieve credentials from Redis
    creds_key = f"{OAUTH_CREDS_PREFIX}{state}"
    credentials_json = redis_client.get(creds_key)

    if not credentials_json:
        return jsonify({"error": "Credentials expired. Please try again."}), 400

    session["credentials"] = json.loads(credentials_json)

    state_key = f"{OAUTH_STATE_PREFIX}{state}"
    redis_client.delete(creds_key)
    redis_client.delete(state_key)

    return jsonify({"success": True})


@app.route("/disconnect")
def disconnect():
    """Disconnect YouTube account"""
    session.pop("credentials", None)
    session.pop("state", None)
    return redirect(url_for("index"))


@app.route("/transfer", methods=["POST"])
def transfer():
    """Transfer playlist from Spotify to YouTube"""
    if "credentials" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    playlist_url = request.json.get("playlist_url")
    if not playlist_url:
        return jsonify({"error": "No playlist URL provided"}), 400

    playlist_id = extract_playlist_id(playlist_url)
    if not playlist_id:
        return jsonify({"error": "Invalid Spotify URL"}), 400

    spotify_client = get_spotify_client()
    if not spotify_client:
        return jsonify({"error": "Spotify credentials not configured"}), 500

    try:
        cached_data = get_cached_playlist(playlist_id)

        if cached_data:
            playlist_name = cached_data["name"]
            playlist_desc = cached_data["description"]
            track_names = cached_data["tracks"]
        else:
            playlist_name, playlist_desc, track_names = fetch_spotify_playlist(
                spotify_client, playlist_id
            )

            if not track_names:
                return jsonify({"error": "Playlist is empty"}), 400

            cache_playlist(
                playlist_id,
                {
                    "name": playlist_name,
                    "description": playlist_desc,
                    "tracks": track_names,
                },
            )

        credentials = Credentials(**session["credentials"])
        youtube = build("youtube", "v3", credentials=credentials)
        yt_playlist_id = create_youtube_playlist(
            youtube, playlist_name, f"Transferred from Spotify\n\n{playlist_desc}"
        )
        if not yt_playlist_id:
            return jsonify({"error": "Failed to create YouTube playlist"}), 500

        return jsonify(
            {
                "playlist_id": yt_playlist_id,
                "playlist_name": playlist_name,
                "total_tracks": len(track_names),
                "tracks": track_names,
            }
        )

    except Exception as e:
        print(f"DEBUG: Transfer error: {e}")
        return jsonify({"error": str(e)}), 500


@app.route("/transfer_track", methods=["POST"])
def transfer_track():
    """Transfer a single track to YouTube playlist"""
    if "credentials" not in session:
        return jsonify({"error": "Not authenticated"}), 401

    data = request.json
    track_name = data.get("track_name")
    playlist_id = data.get("playlist_id")

    if not track_name or not playlist_id:
        return jsonify({"error": "Missing track_name or playlist_id"}), 400

    try:
        credentials = Credentials(**session["credentials"])
        youtube = build("youtube", "v3", credentials=credentials)

        try:
            video_id = search_youtube_music(youtube, track_name)
        except Exception as e:
            if "QUOTA_EXCEEDED" in str(e):
                return jsonify(
                    {
                        "success": False,
                        "quota_exceeded": True,
                        "message": "YouTube API quota exceeded",
                    }
                )
            raise

        if video_id:
            success = add_to_youtube_playlist(youtube, playlist_id, video_id)
            return jsonify({"success": success, "found": True, "video_id": video_id})
        else:
            return jsonify({"success": False, "found": False})

    except Exception as e:
        print(f"DEBUG: Transfer track error: {e}")
        return jsonify({"error": str(e)}), 500


if __name__ == "__main__":
    try:
        redis_client.ping()
        print("✓ Redis connection successful")
    except Exception as e:
        print(f"✗ Redis connection failed: {e}")
        print("Please ensure Redis is running: redis-server")

    app.run(debug=True, port=5000)
