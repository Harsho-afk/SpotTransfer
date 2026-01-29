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

    if (AppState.transferInProgress) {
        return;
    }

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

        const data = await response.json();
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

function updateStatus(message, showSpinner = true) {
    const spinner = showSpinner ? '<span class="spinner"></span> ' : '';
    document.getElementById('currentTrack').innerHTML = spinner + message;
}

function updateStats(added, notFound) {
    document.getElementById('addedTracks').textContent = added;
    document.getElementById('notFoundTracks').textContent = notFound;
}

function updateProgress(current, total) {
    const progress = total === 0 ? 0 : (current / total) * 100;
    const progressFill = document.getElementById('progressFill');
    progressFill.style.width = progress + '%';
    progressFill.textContent = Math.round(progress) + '%';
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
            Remaining ${remainingCount} tracks were not completed and have been added to the "Not Found" list.
            <br><br>
            The YouTube Data API quota resets daily at midnight Pacific Time (PST/PDT).
            You can continue transferring the remaining tracks tomorrow.
        </div>
    `;
}

function showCompletion(notFoundList) {
    updateStatus('Transfer Complete!', false);

    if (notFoundList.length > 0) {
        const notFoundHtml = notFoundList
            .map((track) => `<div>${escapeHtml(track)}</div>`)
            .join('');
        document.getElementById('notFoundContainer').innerHTML = `
            <div class="not-found-section">
                <h3>Tracks Not Found (${notFoundList.length})</h3>
                <div class="not-found-list">
                    ${notFoundHtml}
                </div>
            </div>
        `;
    } else {
        document.getElementById('notFoundContainer').innerHTML = `
            <div class="info-box" style="margin-top: 5px;">
                All tracks processed successfully.
            </div>
        `;
    }
}

function showError(message) {
    alert('Error: ' + message);
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
