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
from flask_wtf.csrf import CSRFProtect

load_dotenv()
if os.environ.get("FLASK_DEVELOPMENT") == "TRUE":
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
csrf = CSRFProtect(app)

SPOTIFY_CACHE_PREFIX = "spottransfer:spotify:"
SPOTIFY_CACHE_TTL = 3600  # 1 hour
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
SPOTIFY_PLAYLIST_REGEX = re.compile(
    r"^https://open\.spotify\.com/playlist/[A-Za-z0-9]+(\?.*)?$"
)


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
            "redirect_uris": os.environ.get("REDIRECT_URI"),
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


def validate_playlist_url(url):
    if not isinstance(url, str):
        return "Playlist URL must be a string"

    url = url.strip()
    if not url:
        return "Playlist URL is required"

    if len(url) > 500:
        return "Playlist URL is too long"

    if not SPOTIFY_PLAYLIST_REGEX.match(url):
        return "Invalid Spotify playlist URL"

    return None


def validate_track_input(track_name, playlist_id):
    if not isinstance(track_name, str) or not track_name.strip():
        return "Invalid track name"

    if len(track_name) > 300:
        return "Track name too long"

    if not isinstance(playlist_id, str) or not playlist_id.strip():
        return "Invalid playlist ID"

    if len(playlist_id) > 100:
        return "Invalid playlist ID"

    return None


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
    flow = Flow.from_client_config(
        get_ytclient_config(),
        scopes=SCOPES,
        redirect_uri=url_for("oauth2callback", _external=True),
    )

    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )

    session["oauth_state"] = state
    return redirect(authorization_url)


@app.route("/oauth2callback")
def oauth2callback():
    state = request.args.get("state")

    if not state or state != session.get("oauth_state"):
        return "Invalid OAuth state", 400

    flow = Flow.from_client_config(
        get_ytclient_config(),
        scopes=SCOPES,
        state=state,
        redirect_uri=url_for("oauth2callback", _external=True),
    )

    flow.fetch_token(authorization_response=request.url)

    credentials = flow.credentials
    session["credentials"] = {
        "token": credentials.token,
        "refresh_token": credentials.refresh_token,
        "token_uri": credentials.token_uri,
        "client_id": credentials.client_id,
        "client_secret": credentials.client_secret,
        "scopes": credentials.scopes,
    }

    session.pop("oauth_state", None)
    return redirect(url_for("index"))


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

    if not request.is_json:
        return jsonify({"error": "Invalid request format"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    playlist_url = data.get("playlist_url")
    error = validate_playlist_url(playlist_url)
    if error:
        return jsonify({"error": error}), 400

    match = re.search(r"playlist/([a-zA-Z0-9]+)", playlist_url)
    playlist_id = match.group(1) if match else None
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

    if not request.is_json:
        return jsonify({"error": "Invalid request format"}), 400

    data = request.get_json(silent=True)
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    track_name = data.get("track_name")
    playlist_id = data.get("playlist_id")

    error = validate_track_input(track_name, playlist_id)
    if error:
        return jsonify({"error": error}), 400

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

    app.run(debug=False, port=5000)
