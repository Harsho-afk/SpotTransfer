const csrfToken = document
    .querySelector('meta[name="csrf-token"]')
    .getAttribute('content');

const AppState = {
    transferInProgress: false,
};

function handleDisconnect() {
    window.location.href = '/disconnect';
}

async function handleStartTransfer() {
    const playlistUrl = document.getElementById('playlistUrl').value.trim();

    if (!playlistUrl) {
        showError('Please enter a Spotify playlist URL');
        return;
    }

    if (!isValidSpotifyPlaylistUrl(playlistUrl)) {
        showError('Please enter a valid Spotify playlist URL');
        return;
    }

    if (AppState.transferInProgress) return;

    AppState.transferInProgress = true;
    disableTransferButton();

    try {
        await transferPlaylist(playlistUrl);
    } catch (error) {
        console.error('Transfer error:', error);
        showError(error.message || 'Transfer failed');
        disableProgressContainer();
    } finally {
        AppState.transferInProgress = false;
        enableTransferButton();
    }
}

async function transferPlaylist(playlistUrl) {
    const response = await fetch('/transfer', {
        method: 'POST',
        headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': csrfToken,
        },
        body: JSON.stringify({ playlist_url: playlistUrl }),
    });

    const data = await response.json();
    if (!response.ok) {
        throw new Error(data.error || 'Transfer failed');
    }

    enableProgressContainer();
    updateStatus('Fetching playlist from Spotify...');
    document.getElementById('totalTracks').textContent = data.total_tracks;

    updateStatus(`Found playlist: ${data.playlist_name}. Starting transfer...`);

    await transferTracks(data.tracks, data.playlist_id, data.total_tracks);
}

async function transferTracks(tracks, playlistId, totalTracks) {
    let added = 0;
    let notFound = 0;
    const notFoundList = [];

    for (let i = 0; i < tracks.length; i++) {
        const track = tracks[i];
        updateStatus(`Searching: ${track}`);

        const result = await transferSingleTrack(track, playlistId);

        if (result?.error === 'Session expired') {
            showError('Session expired. Please sign in again.');
            window.location.reload();
            return;
        }

        if (result.quota_exceeded) {
            const remainingTracks = tracks.slice(i);

            notFound += remainingTracks.length;
            notFoundList.push(...remainingTracks);

            updateStats(added, notFound);
            updateProgress(i, tracks.length);

            showQuotaExceeded(i, added, totalTracks, remainingTracks.length);

            showCompletion(notFoundList);
            return;
        }

        if (result.success && result.found) {
            added++;
            updateStatus(`Added: ${track}`, false);
        } else {
            notFound++;
            notFoundList.push(track);
            updateStatus(`Not found: ${track}`, false);
        }

        updateStats(added, notFound);
        updateProgress(i + 1, tracks.length);

        await sleep(100);
    }

    showCompletion(notFoundList);
}

async function transferSingleTrack(trackName, playlistId) {
    try {
        const response = await fetch('/transfer_track', {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'X-CSRFToken': csrfToken,
            },
            body: JSON.stringify({
                track_name: trackName,
                playlist_id: playlistId,
            }),
        });

        let data;
        try {
            data = await response.json();
        } catch {
            throw new Error('Invalid server response');
        }

        if (!response.ok) {
            throw new Error(data.error || 'Track transfer failed');
        }

        return data;
    } catch (err) {
        console.error('transferSingleTrack error:', err);
        return { success: false, found: false };
    }
}

function enableProgressContainer() {
    document.getElementById('progressContainer').style.display = 'block';
    document.getElementById('quotaWarning').innerHTML = '';
    document.getElementById('notFoundContainer').innerHTML = '';
    document.getElementById('addedTracks').textContent = '0';
    document.getElementById('notFoundTracks').textContent = '0';
    document.getElementById('progressFill').style.width = '0%';
    document.getElementById('progressFill').textContent = '0%';
}

function disableProgressContainer() {
    document.getElementById('progressContainer').style.display = 'none';
}

function disableTransferButton() {
    const btn = document.getElementById('transferBtn');
    btn.disabled = true;
    btn.textContent = 'Transfer in Progress...';
}

function enableTransferButton() {
    const btn = document.getElementById('transferBtn');
    btn.disabled = false;
    btn.textContent = 'Start Transfer';
}

function updateStatus(message, showSpinner = true) {
    const el = document.getElementById('currentTrack');
    if (showSpinner) {
        el.innerHTML = `<span class="spinner"></span> ${escapeHtml(message)}`;
    } else {
        el.textContent = message;
    }
}

function updateStats(added, notFound) {
    document.getElementById('addedTracks').textContent = added;
    document.getElementById('notFoundTracks').textContent = notFound;
}

function updateProgress(current, total) {
    const progress = total === 0 ? 0 : (current / total) * 100;
    const fill = document.getElementById('progressFill');
    fill.style.width = `${progress}%`;
    fill.textContent = `${Math.round(progress)}%`;
}

function showQuotaExceeded(
    currentIndex,
    addedCount,
    totalTracks,
    remainingCount
) {
    document.getElementById('quotaWarning').innerHTML = `
        <div class="error-box">
            <strong>YouTube API quota exceeded</strong>
            <br><br>
            Successfully transferred ${addedCount} out of ${totalTracks} tracks.
            <br>
            Processed ${currentIndex} tracks before quota limit was reached.
            <br>
            Remaining ${remainingCount} tracks were not completed.
            <br><br>
            Quota resets daily at midnight Pacific Time.
        </div>
    `;
}

function showCompletion(notFoundList) {
    updateStatus('Transfer Complete!', false);

    if (notFoundList.length === 0) {
        document.getElementById('notFoundContainer').innerHTML = `
            <p style="color: #4caf50; font-size: 13px; margin-top: 10px;">All tracks transferred successfully.</p>
        `;
        return;
    }

    const html = notFoundList
        .map((track) => `<div>${escapeHtml(track)}</div>`)
        .join('');

    document.getElementById('notFoundContainer').innerHTML = `
        <div class="not-found-section">
            <h3>Tracks Not Found (${notFoundList.length})</h3>
            <div class="not-found-list">${html}</div>
        </div>
    `;
}

function showError(message) {
    alert('Error: ' + message);
}

function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
}

function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

function isValidSpotifyPlaylistUrl(url) {
    return /^https:\/\/open\.spotify\.com\/playlist\/[A-Za-z0-9]+/.test(url);
}
