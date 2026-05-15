/**
 * Planey - Main Application Controller
 * Initializes all components, manages state, and coordinates modules
 */

const App = {
    refreshInterval: null,

    async init() {
        console.log('✈ Planey initializing...');

        // Initialize components
        FlightMap.init();
        Timeline.init();
        Flights.init();

        // Connect WebSocket
        API.connectWS();
        API.onWS((msg) => Flights.handleWSMessage(msg));

        // Load initial data
        await Promise.all([
            Flights.loadAircraft(),
            Flights.loadFlights(),
        ]);
        
        // Load initial trails for map
        Flights.loadInitialTrails();

        // Periodic refresh (every 30s for stats, aircraft cards)
        this.refreshInterval = setInterval(() => {
            Flights.loadAircraft();
            Flights._updateStats();
        }, 30000);

        // Update live relative times (e.g. 10s ago) every second
        setInterval(() => {
            document.querySelectorAll('.live-time-ago').forEach(el => {
                const ts = el.dataset.timestamp;
                if (ts) {
                    el.textContent = Utils.timeAgo(ts);
                }
            });
        }, 1000);

        // History panel listeners
        document.getElementById('history-aircraft-select').addEventListener('change', () => Flights._loadHistory());
        document.getElementById('history-hours').addEventListener('change', () => Flights._loadHistory());

        // Settings
        document.getElementById('btn-settings').addEventListener('click', () => this.showSettings());
        document.getElementById('btn-close-settings').addEventListener('click', () => this.hideSettings());
        document.getElementById('btn-cancel-settings').addEventListener('click', () => this.hideSettings());
        document.getElementById('btn-confirm-settings').addEventListener('click', () => this.saveSettings());

        console.log('✈ Planey ready');
    },

    async showSettings() {
        try {
            const settings = await API.getSettings();
            // Map keys to inputs
            if (settings.polling_interval_seconds) {
                document.getElementById('set-polling-interval').value = settings.polling_interval_seconds;
            }
            if (settings.schedule_sync_interval_minutes) {
                document.getElementById('set-sync-interval').value = settings.schedule_sync_interval_minutes;
            }
            if (settings.reconciliation_interval_minutes) {
                document.getElementById('set-reconciliation-interval').value = settings.reconciliation_interval_minutes;
            }
            document.getElementById('modal-settings').style.display = '';
        } catch (err) {
            Utils.toast('Failed to load settings', 'error');
        }
    },

    hideSettings() {
        document.getElementById('modal-settings').style.display = 'none';
    },

    async saveSettings() {
        const settings = {
            polling_interval_seconds: document.getElementById('set-polling-interval').value,
            schedule_sync_interval_minutes: document.getElementById('set-sync-interval').value,
            reconciliation_interval_minutes: document.getElementById('set-reconciliation-interval').value,
        };

        try {
            await API.updateSettings(settings);
            Utils.toast('Settings saved. Restart container to apply all changes.', 'success');
            this.hideSettings();
        } catch (err) {
            Utils.toast(err.message, 'error');
        }
    }
};

// Boot
document.addEventListener('DOMContentLoaded', () => App.init());
