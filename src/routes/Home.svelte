<script>
    import axios from "axios";
    import { browser } from "$app/environment";
    let playlistLink = "";
    let songs = [];
    let error = "";

    const apiUrl = import.meta.env.VITE_API_URL;

    if (browser && window.location.search.includes("logged_in=true")) {
        window.location.href = "/";
    }

    const fetchPlaylist = async () => {
        if (!browser) return; // Prevent SSR errors

        error = "";
        songs = [];
        if (!playlistLink) {
            error = "Please enter a playlist link";
            return;
        }
        try {
            const response = await axios.get(
                `${apiUrl}/api/playlist?link=${encodeURIComponent(playlistLink)}`,
                { withCredentials: true }, // Enable cookies for session
            );
            songs = response.data;
        } catch (err) {
            console.error(err);
            error = "Failed to fetch playlist. Make sure you are logged in.";
        }
    };

    const login = () => {
        if (browser) window.location.href = `${apiUrl}/login`;
    };

    const logout = async () => {
        try {
            await axios.post(`${apiUrl}/logout`, {}, { withCredentials: true });
            window.location.href = "/";
        } catch (err) {
            console.error("Logout failed:", err);
        }
    };
</script>

<h1>Spotify Playlist Viewer</h1>

{#if browser && document.cookie.includes("session_id")}
    <button on:click={logout}>Logout</button>
{:else}
    <button on:click={login}>Login with Spotify</button>
{/if}

<div>
    <input placeholder="Enter Playlist Link" bind:value={playlistLink} />
    <button on:click={fetchPlaylist}>Fetch Playlist</button>
</div>

{#if error}
    <p style="color: red;">{error}</p>
{/if}

<div class="playlist">
    {#each songs as song}
        <div class="song">
            <img src={song.image} alt="Album cover" />
            <h3>{song.name}</h3>
            <p>{song.artist}</p>
            <p><i>{song.album}</i></p>
        </div>
    {/each}
</div>

<style>
    .playlist {
        display: grid;
        gap: 1rem;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
        margin-top: 20px;
    }
    .song {
        text-align: center;
        border: 1px solid #eee;
        border-radius: 8px;
        padding: 10px;
        box-shadow: 0 0 5px rgba(0, 0, 0, 0.1);
    }
    .song img {
        max-width: 100%;
        border-radius: 5px;
    }
</style>
