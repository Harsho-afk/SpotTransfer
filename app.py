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
from flask_wtf.csrf import CSRFProtect
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
import logging
import os
import re
import json
import time
import redis
from datetime import timedelta
from dotenv import load_dotenv
import spotipy
from spotipy.oauth2 import SpotifyClientCredentials
from spotipy.exceptions import SpotifyException
from google_auth_oauthlib.flow import Flow
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

load_dotenv()

if os.environ.get("FLASK_DEVELOPMENT") == "TRUE":
    os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = "1"

SPOTIFY_CACHE_PREFIX = "spottransfer:spotify:"
SPOTIFY_CACHE_TTL = 3600  # 1 hour
SCOPES = ["https://www.googleapis.com/auth/youtube.force-ssl"]
SPOTIFY_PLAYLIST_REGEX = re.compile(
    r"^https://open\.spotify\.com/playlist/[A-Za-z0-9]+(\?.*)?$"
)


app = Flask(__name__)
app.logger.setLevel(logging.CRITICAL)


def create_redis_client(decode_responses=True):
    """Create a Redis client with common configuration"""
    return redis.Redis(
        host=os.environ.get("REDIS_HOST", "localhost"),
        port=int(os.environ.get("REDIS_PORT", 6379)),
        db=int(os.environ.get("REDIS_DB", 0)),
        username=os.environ.get("REDIS_USERNAME", "default"),
        password=os.environ.get("REDIS_PASSWORD", None),
        decode_responses=decode_responses,
    )


redis_session_client = create_redis_client(decode_responses=False)  # binary data
redis_client = create_redis_client(decode_responses=True)  # string data

app.config.update(
    SECRET_KEY=os.environ.get("FLASK_SECRET_KEY"),
    SESSION_TYPE="redis",
    SESSION_REDIS=redis_session_client,
    SESSION_USE_SIGNER=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not app.debug,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=6),
)

Session(app)
csrf = CSRFProtect(app)

limiter = Limiter(
    key_func=get_remote_address,
    app=app,
    storage_uri=os.environ.get("REDIS_URL"),
    default_limits=["200 per day", "50 per hour"],
)


def internal_error(message="Internal server error"):
    return jsonify({"error": message}), 500


def is_quota_exceeded(error: HttpError) -> bool:
    """Detect YouTube Data API quota errors"""
    try:
        if error.resp.status != 403:
            return False

        error_content = json.loads(error.content.decode("utf-8"))
        reasons = [
            err.get("reason", "")
            for err in error_content.get("error", {}).get("errors", [])
        ]

        return any(
            reason in ("quotaExceeded", "dailyLimitExceeded") for reason in reasons
        )
    except Exception:
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


def fetch_spotify_playlist(spotify_client, playlist_id):
    """Fetch playlist data from Spotify API"""
    try:
        playlist_info = spotify_client.playlist(playlist_id)
    except SpotifyException as e:
        if e.http_status == 404:
            raise ValueError("Spotify playlist not found. Please check the URL.")
        elif e.http_status == 401:
            raise ValueError(
                "Spotify authentication failed. Please check API credentials."
            )
        elif e.http_status == 403:
            raise ValueError("Access to this Spotify playlist is forbidden.")
        else:
            raise ValueError(f"Spotify API error: {str(e)}")

    playlist_name = playlist_info["name"]
    playlist_desc = playlist_info.get("description", "")
    tracks = []

    try:
        results = spotify_client.playlist_tracks(playlist_id)
        tracks.extend(results["items"])

        while results["next"]:
            results = spotify_client.next(results)
            tracks.extend(results["items"])
    except SpotifyException as e:
        raise ValueError(f"Failed to fetch playlist tracks: {str(e)}")

    if not tracks:
        return None, None, []

    track_names = []
    for track in tracks:
        if track["track"] and track["track"]["name"]:
            artists = ", ".join(artist["name"] for artist in track["track"]["artists"])
            track_names.append(f"{track['track']['name']} - {artists}")

    return playlist_name, playlist_desc, track_names


def get_cached_playlist(playlist_id):
    """Get cached playlist data from Redis"""
    try:
        cache_key = f"{SPOTIFY_CACHE_PREFIX}playlist:{playlist_id}"
        cached_data = redis_client.get(cache_key)
        if cached_data:
            redis_client.expire(cache_key, SPOTIFY_CACHE_TTL)
            return json.loads(cached_data)
        return None
    except redis.RedisError as e:
        app.logger.debug(f"Redis cache read error: {e}")
        return None
    except json.JSONDecodeError as e:
        app.logger.debug(f"Cache data decode error: {e}")
        return None
    except Exception as e:
        app.logger.debug(f"Unexpected cache error: {e}")
        return None


def cache_playlist(playlist_id, data):
    """Cache playlist data in Redis"""
    try:
        cache_key = f"{SPOTIFY_CACHE_PREFIX}playlist:{playlist_id}"
        redis_client.setex(cache_key, SPOTIFY_CACHE_TTL, json.dumps(data))
    except redis.RedisError as e:
        app.logger.debug(f"Redis cache write error: {e}")
    except Exception as e:
        app.logger.debug(f"Unexpected cache write error: {e}")


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
            try:
                redis_client.setex(cache_key, SPOTIFY_CACHE_TTL, video_id)
            except redis.RedisError as e:
                app.logger.debug(f"Failed to cache search result: {e}")
            return video_id

        return None

    except HttpError as e:
        if is_quota_exceeded(e):
            raise Exception("QUOTA_EXCEEDED")
        app.logger.debug("YouTube search failed", exc_info=True)
        return None
    except Exception as e:
        app.logger.debug(f"Unexpected error in YouTube search: {e}")
        return None


def create_youtube_playlist(youtube, title, description):
    """Create a new YouTube playlist"""
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

    except HttpError as e:
        if is_quota_exceeded(e):
            raise Exception("QUOTA_EXCEEDED")
        app.logger.exception("Error creating YouTube playlist")
        raise Exception(f"YouTube API error: {e.resp.status}")
    except Exception as e:
        app.logger.exception(f"Unexpected error creating YouTube playlist: {e}")
        raise


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

            elif is_quota_exceeded(e):
                raise Exception("QUOTA_EXCEEDED")

            elif status_code >= 500:
                if attempt < max_retries - 1:
                    wait_time = (attempt + 1) * 2
                    time.sleep(wait_time)
                    continue
                else:
                    return False
            else:
                app.logger.debug(
                    f"Error adding video to playlist (status {status_code}): {e}"
                )
                return False

        except Exception as e:
            app.logger.debug(f"Unexpected error adding video to playlist: {e}")
            if attempt < max_retries - 1:
                time.sleep(2)
                continue
            return False

    return False


@app.errorhandler(429)
def rate_limit_exceeded(e):
    return (
        jsonify({"error": "Too many requests. Please slow down and try again later."}),
        429,
    )


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
    """Start OAuth flow for YouTube authentication"""
    try:
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
    except Exception as e:
        app.logger.exception(f"Error starting OAuth flow: {e}")
        return (
            jsonify(
                {
                    "error": "Failed to start authentication. Please check your Google API credentials."
                }
            ),
            500,
        )


@app.route("/oauth2callback")
def oauth2callback():
    """Handle OAuth callback from Google"""
    state = request.args.get("state")

    if not state or state != session.get("oauth_state"):
        return "Invalid OAuth state. Please try authenticating again.", 400
    try:
        flow = Flow.from_client_config(
            get_ytclient_config(),
            scopes=SCOPES,
            state=state,
            redirect_uri=url_for("oauth2callback", _external=True),
        )

        flow.fetch_token(authorization_response=request.url)
        credentials = flow.credentials

        session.clear()
        session.permanent = True
        session["credentials"] = {
            "token": credentials.token,
            "refresh_token": credentials.refresh_token,
            "token_uri": credentials.token_uri,
            "client_id": credentials.client_id,
            "client_secret": credentials.client_secret,
            "scopes": credentials.scopes,
        }

        return redirect(url_for("index"))

    except Exception as e:
        app.logger.exception(f"OAuth token exchange failed: {e}")
        session.clear()
        return "Authentication failed. Please try again.", 500


@app.route("/disconnect")
@limiter.limit("10 per minute")
def disconnect():
    """Disconnect YouTube account"""
    session.clear()
    return redirect(url_for("index"))


@app.route("/transfer", methods=["POST"])
@limiter.limit("5 per hour")
def transfer():
    """Transfer a Spotify playlist to YouTube Music"""
    if "credentials" not in session:
        return (
            jsonify(
                {"error": "Not authenticated. Please connect your YouTube account."}
            ),
            401,
        )

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
        return jsonify({"error": "Invalid Spotify playlist URL format"}), 400

    spotify_client = get_spotify_client()
    if not spotify_client:
        return internal_error("Spotify API credentials not configured properly")

    try:
        # Try to get cached playlist data first
        cached_data = get_cached_playlist(playlist_id)

        if cached_data:
            playlist_name = cached_data["name"]
            playlist_desc = cached_data["description"]
            track_names = cached_data["tracks"]
        else:
            # Fetch from Spotify API
            try:
                playlist_name, playlist_desc, track_names = fetch_spotify_playlist(
                    spotify_client, playlist_id
                )
            except ValueError as e:
                # User-facing Spotify errors
                return jsonify({"error": str(e)}), 400
            except SpotifyException as e:
                app.logger.exception(f"Spotify API error: {e}")
                return (
                    jsonify(
                        {
                            "error": "Failed to fetch playlist from Spotify. Please try again."
                        }
                    ),
                    500,
                )

            if not track_names:
                return (
                    jsonify({"error": "Playlist is empty or has no accessible tracks"}),
                    400,
                )

            # Cache the playlist data
            cache_playlist(
                playlist_id,
                {
                    "name": playlist_name,
                    "description": playlist_desc,
                    "tracks": track_names,
                },
            )

        # Validate credentials
        creds_data = session.get("credentials")
        if not isinstance(creds_data, dict):
            session.clear()
            return (
                jsonify(
                    {"error": "Session expired. Please reconnect your YouTube account."}
                ),
                401,
            )

        # Build YouTube client
        try:
            youtube = build("youtube", "v3", credentials=Credentials(**creds_data))
        except Exception as e:
            app.logger.exception(f"Failed to build YouTube client: {e}")
            session.clear()
            return (
                jsonify(
                    {
                        "error": "Failed to authenticate with YouTube. Please reconnect your account."
                    }
                ),
                401,
            )

        # Create YouTube playlist
        try:
            yt_playlist_id = create_youtube_playlist(
                youtube,
                playlist_name,
                f"Transferred from Spotify\n\n{playlist_desc}",
            )
        except Exception as e:
            if "QUOTA_EXCEEDED" in str(e):
                return (
                    jsonify(
                        {
                            "error": "YouTube API quota exceeded. The quota resets daily at midnight Pacific Time. Please try again later."
                        }
                    ),
                    429,
                )
            app.logger.exception("Failed to create YouTube playlist")
            return internal_error(
                "Failed to create YouTube playlist. Please try again."
            )

        if not yt_playlist_id:
            return internal_error("Failed to create YouTube playlist")

        return jsonify(
            {
                "playlist_id": yt_playlist_id,
                "playlist_name": playlist_name,
                "total_tracks": len(track_names),
                "tracks": track_names,
            }
        )

    except HttpError as e:
        app.logger.exception("YouTube API error during transfer")
        if is_quota_exceeded(e):
            return (
                jsonify(
                    {
                        "error": "YouTube API quota exceeded. The quota resets daily at midnight Pacific Time. Please try again later."
                    }
                ),
                429,
            )
        return internal_error("YouTube API error occurred. Please try again.")

    except redis.RedisError as e:
        app.logger.exception(f"Redis error during transfer: {e}")
        return internal_error("Cache error occurred. Please try again.")

    except Exception as e:
        app.logger.exception(f"Unexpected error during playlist transfer: {e}")
        return internal_error("An unexpected error occurred. Please try again.")


@app.route("/transfer_track", methods=["POST"])
@limiter.limit("30 per minute")
def transfer_track():
    """Transfer a single track to YouTube Music playlist"""
    if "credentials" not in session:
        return (
            jsonify(
                {"error": "Not authenticated. Please reconnect your YouTube account."}
            ),
            401,
        )

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
        creds_data = session.get("credentials")
        if not isinstance(creds_data, dict):
            session.clear()
            return (
                jsonify(
                    {"error": "Session expired. Please reconnect your YouTube account."}
                ),
                401,
            )

        try:
            youtube = build("youtube", "v3", credentials=Credentials(**creds_data))
        except Exception as e:
            app.logger.exception(f"Failed to build YouTube client: {e}")
            session.clear()
            return (
                jsonify(
                    {"error": "Session expired. Please reconnect your YouTube account."}
                ),
                401,
            )

        try:
            video_id = search_youtube_music(youtube, track_name)
        except Exception as e:
            if "QUOTA_EXCEEDED" in str(e):
                return jsonify(
                    {
                        "success": False,
                        "quota_exceeded": True,
                        "message": "YouTube API quota exceeded. The quota resets daily at midnight Pacific Time.",
                    }
                )
            raise

        if video_id:
            try:
                success = add_to_youtube_playlist(youtube, playlist_id, video_id)
                return jsonify({"success": success, "found": True})
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
        else:
            return jsonify({"success": False, "found": False})

    except HttpError as e:
        app.logger.exception("YouTube API error during track transfer")
        if is_quota_exceeded(e):
            return jsonify(
                {
                    "success": False,
                    "quota_exceeded": True,
                    "message": "YouTube API quota exceeded",
                }
            )
        return jsonify({"success": False, "found": False, "error": "YouTube API error"})

    except Exception as e:
        app.logger.exception(f"Unexpected error during track transfer: {e}")
        return jsonify(
            {"success": False, "found": False, "error": "An unexpected error occurred"}
        )


if __name__ == "__main__":
    try:
        redis_client.ping()
        app.logger.info("Redis connection successful")
    except Exception:
        app.logger.warning("✗ Redis connection failed")
        app.logger.warning("Please ensure Redis is running: redis-server")

    app.run(debug=True, port=5000)
