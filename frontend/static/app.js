/**
 * AEVE Frontend — Interactive pipeline UI with SSE real-time progress.
 */

// ─── DOM Elements ──────────────────────────────────────────
const form = document.getElementById('pipelineForm');
const queryInput = document.getElementById('queryInput');
const imageInput = document.getElementById('imageInput');
const fileUpload = document.getElementById('fileUpload');
const fileName = document.getElementById('fileName');
const submitBtn = document.getElementById('submitBtn');
const statusBadge = document.getElementById('statusBadge');
const inputSection = document.getElementById('inputSection');
const pipelineSection = document.getElementById('pipelineSection');
const resultSection = document.getElementById('resultSection');
const errorSection = document.getElementById('errorSection');
const logContent = document.getElementById('logContent');
const logContainer = document.getElementById('logContainer');
const logToggle = document.getElementById('logToggle');
const logToggleBtn = document.getElementById('logToggleBtn');
const elapsedTime = document.getElementById('elapsedTime');

let selectedQuality = 'low';
let timerInterval = null;
let startTime = null;

// ─── File Upload ───────────────────────────────────────────
fileUpload.addEventListener('click', () => imageInput.click());

fileUpload.addEventListener('dragover', (e) => {
    e.preventDefault();
    fileUpload.style.borderColor = 'var(--accent-primary)';
});

fileUpload.addEventListener('dragleave', () => {
    fileUpload.style.borderColor = '';
});

fileUpload.addEventListener('drop', (e) => {
    e.preventDefault();
    fileUpload.style.borderColor = '';
    const files = e.dataTransfer.files;
    if (files.length > 0) {
        imageInput.files = files;
        onFileSelect(files[0]);
    }
});

imageInput.addEventListener('change', () => {
    if (imageInput.files.length > 0) {
        onFileSelect(imageInput.files[0]);
    }
});

function onFileSelect(file) {
    fileName.textContent = file.name;
    fileUpload.classList.add('has-file');
}

// ─── Quality Selector ──────────────────────────────────────
document.querySelectorAll('.quality-btn').forEach(btn => {
    btn.addEventListener('click', () => {
        document.querySelectorAll('.quality-btn').forEach(b => b.classList.remove('active'));
        btn.classList.add('active');
        selectedQuality = btn.dataset.quality;
    });
});

// ─── Log Toggle ────────────────────────────────────────────
logToggle.addEventListener('click', () => {
    logContainer.classList.toggle('open');
    logToggleBtn.classList.toggle('open');
});

// ─── Timer ─────────────────────────────────────────────────
function startTimer() {
    startTime = Date.now();
    timerInterval = setInterval(() => {
        const elapsed = Math.floor((Date.now() - startTime) / 1000);
        const min = Math.floor(elapsed / 60);
        const sec = elapsed % 60;
        elapsedTime.textContent = `${min}:${sec.toString().padStart(2, '0')}`;
    }, 1000);
}

function stopTimer() {
    if (timerInterval) clearInterval(timerInterval);
}

// ─── Form Submit ───────────────────────────────────────────
form.addEventListener('submit', async (e) => {
    e.preventDefault();

    const query = queryInput.value.trim();
    const hasImage = imageInput.files.length > 0;

    if (!query && !hasImage) {
        queryInput.focus();
        queryInput.style.borderColor = 'var(--error)';
        setTimeout(() => queryInput.style.borderColor = '', 2000);
        return;
    }

    // Prepare form data
    const formData = new FormData();
    formData.append('query', query);
    formData.append('quality', selectedQuality);
    if (hasImage) {
        formData.append('image', imageInput.files[0]);
    }

    // Disable form
    submitBtn.disabled = true;
    submitBtn.querySelector('.btn-text').textContent = 'Starting...';
    statusBadge.textContent = 'Processing';

    try {
        // Start the pipeline
        const res = await fetch('/start', { method: 'POST', body: formData });
        const data = await res.json();

        if (data.error) {
            alert(data.error);
            submitBtn.disabled = false;
            submitBtn.querySelector('.btn-text').textContent = 'Generate Video';
            return;
        }

        // Show pipeline UI
        inputSection.style.display = 'none';
        pipelineSection.style.display = 'block';

        // Open log by default
        logContainer.classList.add('open');
        logToggleBtn.classList.add('open');

        // Start timer
        startTimer();

        // Connect to SSE
        connectSSE(data.job_id);

    } catch (err) {
        alert('Failed to start: ' + err.message);
        submitBtn.disabled = false;
        submitBtn.querySelector('.btn-text').textContent = 'Generate Video';
    }
});

// ─── SSE Connection ────────────────────────────────────────
function connectSSE(jobId) {
    const evtSource = new EventSource(`/events/${jobId}`);

    evtSource.onmessage = (e) => {
        const event = JSON.parse(e.data);
        handleEvent(event);

        if (event.type === 'end' || event.type === 'error') {
            evtSource.close();
            stopTimer();
        }
    };

    evtSource.onerror = () => {
        evtSource.close();
        stopTimer();
    };
}

// ─── Event Handler ─────────────────────────────────────────
function handleEvent(event) {
    switch (event.type) {
        case 'phase':
            handlePhaseEvent(event.data);
            break;
        case 'log':
            appendLog(event.data);
            break;
        case 'complete':
            handleComplete(event.data);
            break;
        case 'error':
            handleError(event.data);
            break;
        case 'heartbeat':
            break;
    }
}

function handlePhaseEvent(data) {
    const { phase, status, title, detail, preview, scenes } = data;
    const step = document.getElementById(`step-${phase}`);
    const badge = document.getElementById(`badge-${phase}`);
    const detailEl = document.getElementById(`detail-${phase}`);
    const previewEl = document.getElementById(`preview-${phase}`);

    if (!step) return;

    // Update step state
    step.className = `step ${status}`;

    // Update badge
    if (status === 'running') {
        badge.textContent = 'Running...';
        statusBadge.textContent = title;
    } else if (status === 'done') {
        badge.textContent = '✓ Done';
    }

    // Update detail
    if (detail) {
        detailEl.textContent = detail;
    }

    // Show preview
    if (preview) {
        previewEl.textContent = preview;
        previewEl.classList.add('visible');
    }

    if (scenes) {
        previewEl.textContent = `${scenes} scenes created in Scene Manifest`;
        previewEl.classList.add('visible');
    }
}

function appendLog(data) {
    const { level, message } = data;
    const line = document.createElement('div');
    line.className = `log-line ${level}`;

    // Color code parts of the message
    let html = message
        .replace(/✓/g, '<span class="success-mark">✓</span>')
        .replace(/✗/g, '<span style="color:var(--error)">✗</span>')
        .replace(/⚠/g, '<span style="color:var(--warning)">⚠</span>')
        .replace(/⏳/g, '<span style="color:var(--accent-secondary)">⏳</span>')
        .replace(/\[([A-Z]+)\]/g, '<span style="color:var(--accent-primary)">[<span>$1</span>]</span>');

    line.innerHTML = html;
    logContent.appendChild(line);
    logContent.scrollTop = logContent.scrollHeight;
}

function handleComplete(data) {
    statusBadge.textContent = 'Complete ✓';

    // Show result
    pipelineSection.style.display = 'none';
    resultSection.style.display = 'block';

    const video = document.getElementById('resultVideo');
    video.src = data.video_path;
}

function handleError(data) {
    statusBadge.textContent = 'Error';

    pipelineSection.style.display = 'none';
    errorSection.style.display = 'block';

    document.getElementById('errorMessage').textContent = 
        `${data.type}: ${data.message}`;
}
