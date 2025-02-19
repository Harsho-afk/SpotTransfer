from spotipy.oauth2 import SpotifyClientCredentials
from dotenv import load_dotenv
import os
import spotipy
from ytmusicapi import YTMusic, OAuthCredentials
import time

load_dotenv()

S_ID = os.getenv('S_ID')
S_SECRET = os.getenv('S_SECRET')
Y_ID = os.getenv('Y_ID')
Y_SECRET = os.getenv('Y_SECRET')

cred_mgr = SpotifyClientCredentials(client_id=S_ID, client_secret=S_SECRET)
sp = spotipy.Spotify(auth_manager=cred_mgr)

playlist_link = "https://open.spotify.com/playlist/7MvoTLHVkJzFJtpE0WoWQm?si=6_7Z8STkTZWb2acGnn72zw"
playlist_id = playlist_link.split('/')[-1].split('?')[0]
playlist = sp.playlist_tracks(playlist_id)

tracks = []
limit = 100
offset = 0

while True:
    playlist_data = sp.playlist_tracks(playlist_id, limit=limit, offset=offset)
    tracks.extend(playlist_data["items"])
    print(f"Fetched {len(playlist_data['items'])
                     } tracks, Total: {len(tracks)}")
    if not playlist_data["next"]:
        break
    offset += limit

spotify_songs = [
    f"{track['track']['name']} - {', '.join(artist['name']
                                            for artist in track['track']['artists'])}"
    for track in tracks
]

ytmusic = YTMusic("oauth.json", oauth_credentials=OAuthCredentials(
    client_id=Y_ID, client_secret=Y_SECRET))

yt_playlist_id = ytmusic.create_playlist(
    "Spotify Transfer", "Songs from Spotify playlist")

for song in spotify_songs:
    search_results = ytmusic.search(song, filter="songs")
    if search_results:
        song_id = search_results[0]["videoId"]
        ytmusic.add_playlist_items(yt_playlist_id, [song_id])
        print(f"Added: {song}")
        time.sleep(1)
    else:
        print(f"Not found: {song}")

print("Transfer complete!")
