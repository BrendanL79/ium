// Global state
let socket;
let status = {};
let isDaemonRunning = false;

// Initialize Socket.IO connection
function initSocket() {
    socket = io();
    
    socket.on('connect', () => {
        console.log('Connected to server');
        updateConnectionStatus(true);
    });
    
    socket.on('disconnect', () => {
        console.log('Disconnected from server');
        updateConnectionStatus(false);
    });
    
    socket.on('status_update', (data) => {
        updateStatus(data);
    });
    
    socket.on('check_complete', (data) => {
        handleCheckComplete(data);
    });
    
    socket.on('connected', (data) => {
        addLog('Connected: ' + data.status, 'info');
    });
}

// Update connection status indicator
function updateConnectionStatus(connected) {
    const dot = document.getElementById('status-indicator');
    const text = document.getElementById('status-text');
    
    if (connected) {
        dot.classList.add('connected');
        text.textContent = 'Connected';
        loadStatus();
    } else {
        dot.classList.remove('connected');
        dot.classList.remove('checking');
        text.textContent = 'Disconnected';
    }
}

// Update status from server
function updateStatus(data) {
    const dot = document.getElementById('status-indicator');
    
    if (data.checking) {
        dot.classList.add('checking');
        document.getElementById('status-text').textContent = 'Checking...';
        document.getElementById('check-now').disabled = true;
    } else {
        dot.classList.remove('checking');
        document.getElementById('status-text').textContent = 'Connected';
        document.getElementById('check-now').disabled = false;
    }
    
    if (data.daemon_running !== undefined) {
        isDaemonRunning = data.daemon_running;
        updateDaemonButton();
    }
    
    if (data.last_check) {
        updateLastCheck(data.last_check);
    }
}

// Load current status
async function loadStatus() {
    try {
        const response = await fetch('/api/status');
        const data = await response.json();
        
        // Update mode indicator
        const modeIndicator = document.getElementById('mode-indicator');
        if (data.dry_run) {
            modeIndicator.textContent = 'DRY RUN MODE';
            modeIndicator.className = 'mode dry-run';
        } else {
            modeIndicator.textContent = 'PRODUCTION MODE';
            modeIndicator.className = 'mode production';
        }
        
        isDaemonRunning = data.daemon_running;
        updateDaemonButton();
        
        if (data.last_check) {
            updateLastCheck(data.last_check);
        }
        
        // Load other data
        loadConfig();
        loadState();
        loadHistory();
        loadUpdates();
        
    } catch (error) {
        addLog('Failed to load status: ' + error, 'error');
    }
}

// Update last check time
function updateLastCheck(timestamp) {
    const elem = document.getElementById('last-check');
    const date = new Date(timestamp);
    elem.textContent = 'Last check: ' + date.toLocaleString();
}

// Update daemon button state
function updateDaemonButton() {
    const btn = document.getElementById('toggle-daemon');
    if (isDaemonRunning) {
        btn.textContent = 'Stop Daemon';
        btn.classList.add('btn-danger');
        btn.classList.remove('btn-secondary');
    } else {
        btn.textContent = 'Start Daemon';
        btn.classList.remove('btn-danger');
        btn.classList.add('btn-secondary');
    }
}

// Handle check complete event
function handleCheckComplete(data) {
    addLog('Check complete', 'info');
    updateLastCheck(data.timestamp);
    
    if (data.updates && data.updates.length > 0) {
        displayUpdates(data.updates);
        addLog(`Found ${data.updates.length} updates`, 'warning');
    } else {
        displayNoUpdates();
        addLog('No updates found', 'info');
    }
    
    loadHistory();
}

// Display available updates
function displayUpdates(updates) {
    const container = document.getElementById('update-list');
    container.innerHTML = '';
    
    updates.forEach(update => {
        const item = document.createElement('div');
        item.className = 'update-item';
        item.innerHTML = `
            <h3>${update.image}</h3>
            <div class="update-details">
                <span>Base tag: <strong>${update.base_tag}</strong></span>
                <span class="tag-change">${update.old_tag} → ${update.new_tag}</span>
                <span>Digest: ${update.digest.substring(0, 12)}...</span>
            </div>
        `;
        container.appendChild(item);
    });
}

// Display no updates message
function displayNoUpdates() {
    document.getElementById('update-list').innerHTML = 
        '<p class="no-updates">All images are up to date!</p>';
}

// Load configuration
async function loadConfig() {
    try {
        const response = await fetch('/api/config');
        const config = await response.json();
        document.getElementById('config-json').value = JSON.stringify(config, null, 2);
    } catch (error) {
        addLog('Failed to load config: ' + error, 'error');
    }
}

// Save configuration
async function saveConfig() {
    try {
        const configText = document.getElementById('config-json').value;
        const config = JSON.parse(configText);
        
        const response = await fetch('/api/config', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(config)
        });
        
        if (response.ok) {
            addLog('Configuration saved successfully', 'info');
            loadStatus();
        } else {
            const error = await response.json();
            addLog('Failed to save config: ' + error.error, 'error');
        }
    } catch (error) {
        addLog('Invalid JSON: ' + error, 'error');
    }
}

// Load current state
async function loadState() {
    try {
        const response = await fetch('/api/state');
        const state = await response.json();
        document.getElementById('state-view').textContent = JSON.stringify(state, null, 2);
    } catch (error) {
        addLog('Failed to load state: ' + error, 'error');
    }
}

// Load update history
async function loadHistory() {
    try {
        const response = await fetch('/api/history');
        const history = await response.json();
        
        const container = document.getElementById('history-list');
        if (history.length === 0) {
            container.innerHTML = '<p class="no-updates">No update history yet.</p>';
            return;
        }
        
        container.innerHTML = '';
        history.reverse().forEach(item => {
            const elem = document.createElement('div');
            elem.className = 'history-item';
            const date = new Date(item.timestamp);
            elem.innerHTML = `
                <span class="history-time">${date.toLocaleString()}</span>
                <span>${item.image}</span>
                <span class="tag-change">${item.old_tag} → ${item.new_tag}</span>
                <span>${item.applied ? '✅ Applied' : '⚠️ Dry run'}</span>
            `;
            container.appendChild(elem);
        });
    } catch (error) {
        addLog('Failed to load history: ' + error, 'error');
    }
}

// Load last updates
async function loadUpdates() {
    try {
        const response = await fetch('/api/updates');
        const data = await response.json();
        
        if (data.updates && data.updates.length > 0) {
            displayUpdates(data.updates);
        } else {
            displayNoUpdates();
        }
    } catch (error) {
        addLog('Failed to load updates: ' + error, 'error');
    }
}

// Trigger manual check
async function checkNow() {
    try {
        addLog('Starting manual check...', 'info');
        const response = await fetch('/api/check', { method: 'POST' });
        
        if (!response.ok) {
            const error = await response.json();
            addLog('Check failed: ' + error.error, 'error');
        }
    } catch (error) {
        addLog('Failed to start check: ' + error, 'error');
    }
}

// Toggle daemon mode
async function toggleDaemon() {
    try {
        const action = isDaemonRunning ? 'stop' : 'start';
        const interval = document.getElementById('daemon-interval').value;
        
        const response = await fetch('/api/daemon', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ action, interval: parseInt(interval) })
        });
        
        if (response.ok) {
            const result = await response.json();
            addLog(`Daemon ${result.status}`, 'info');
            isDaemonRunning = action === 'start';
            updateDaemonButton();
        } else {
            const error = await response.json();
            addLog('Daemon control failed: ' + error.error, 'error');
        }
    } catch (error) {
        addLog('Failed to control daemon: ' + error, 'error');
    }
}

// Add log entry
function addLog(message, level = 'info') {
    const logOutput = document.getElementById('log-output');
    const entry = document.createElement('div');
    entry.className = `log-line ${level}`;
    const timestamp = new Date().toLocaleTimeString();
    entry.textContent = `[${timestamp}] ${message}`;
    logOutput.appendChild(entry);
    logOutput.scrollTop = logOutput.scrollHeight;
    
    // Keep only last 100 lines
    while (logOutput.children.length > 100) {
        logOutput.removeChild(logOutput.firstChild);
    }
}

// Tab switching
function switchTab(tabName) {
    // Update tab buttons
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.classList.remove('active');
        if (btn.dataset.tab === tabName) {
            btn.classList.add('active');
        }
    });
    
    // Update tab panes
    document.querySelectorAll('.tab-pane').forEach(pane => {
        pane.classList.remove('active');
        if (pane.id === tabName) {
            pane.classList.add('active');
        }
    });
}

// Initialize everything when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
    initSocket();
    
    // Event listeners
    document.getElementById('check-now').addEventListener('click', checkNow);
    document.getElementById('toggle-daemon').addEventListener('click', toggleDaemon);
    document.getElementById('refresh-config').addEventListener('click', loadConfig);
    document.getElementById('save-config').addEventListener('click', saveConfig);
    
    // Tab switching
    document.querySelectorAll('.tab-button').forEach(btn => {
        btn.addEventListener('click', () => switchTab(btn.dataset.tab));
    });
    
    addLog('Web UI initialized', 'info');
});