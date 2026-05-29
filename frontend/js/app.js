/**
 * Planey - Main Application Controller
 * Initializes all components, manages state, and coordinates modules
 */

const App = {
    refreshInterval: null,

    async init() {
        console.log('✈ Planey initializing...');

        Utils.loadTimezone();

        // Initialize components
        await FlightMap.init();
        POIs.init();
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
        await Flights.loadInitialTrails();
        
        // Adjust default map view based on active tracks or last known positions
        FlightMap.updateDefaultMapView();

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
        document.getElementById('history-lookback-select').addEventListener('change', () => Flights._loadHistory());

        // Settings
        document.getElementById('btn-settings').addEventListener('click', () => this.showSettings());
        document.getElementById('btn-close-settings').addEventListener('click', () => this.hideSettings());
        document.getElementById('btn-cancel-settings').addEventListener('click', () => this.hideSettings());
        document.getElementById('btn-confirm-settings').addEventListener('click', () => this.saveSettings());
        document.getElementById('btn-reconcile-all').addEventListener('click', () => this.triggerReconcileAll());

        // Telemetry Auditor
        document.getElementById('btn-telemetry-auditor').addEventListener('click', () => {
            if (window.TelemetryAuditor) window.TelemetryAuditor.open();
        });
        document.getElementById('btn-close-auditor').addEventListener('click', () => {
            if (window.TelemetryAuditor) window.TelemetryAuditor.close();
        });

        console.log('✈ Planey ready');
    },

    async showSettings() {
        try {
            const settings = await API.getSettings();
            // Map keys to inputs
            if (settings.polling_interval_seconds) {
                document.getElementById('set-polling-interval').value = settings.polling_interval_seconds;
            }
            if (settings.polling_interval_passive_seconds) {
                document.getElementById('set-polling-interval-passive').value = settings.polling_interval_passive_seconds;
            } else {
                document.getElementById('set-polling-interval-passive').value = '300';
            }
            document.getElementById('set-manual-airborne-mode').checked = (settings.manual_airborne_mode === 'true');

            if (settings.schedule_sync_interval_minutes) {
                document.getElementById('set-sync-interval').value = settings.schedule_sync_interval_minutes;
            }
            if (settings.reconciliation_interval_minutes) {
                document.getElementById('set-reconciliation-interval').value = settings.reconciliation_interval_minutes;
            }
            // Data retention settings
            document.getElementById('set-position-retention-days').value = settings.position_retention_days || '90';
            document.getElementById('set-history-retention-days').value = settings.flight_history_retention_days || '90';

            document.getElementById('set-timezone').value = Utils.getTimezone();

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
            polling_interval_passive_seconds: document.getElementById('set-polling-interval-passive').value,
            manual_airborne_mode: document.getElementById('set-manual-airborne-mode').checked ? 'true' : 'false',
            schedule_sync_interval_minutes: document.getElementById('set-sync-interval').value,
            reconciliation_interval_minutes: document.getElementById('set-reconciliation-interval').value,
            position_retention_days: document.getElementById('set-position-retention-days').value,
            flight_history_retention_days: document.getElementById('set-history-retention-days').value,
        };

        try {
            await API.updateSettings(settings);
            Utils.setTimezone(document.getElementById('set-timezone').value);
            Utils.toast('Settings saved and applied successfully.', 'success');
            this.hideSettings();
        } catch (err) {
            Utils.toast(err.message, 'error');
        }
    },

    async triggerReconcileAll() {
        const btn = document.getElementById('btn-reconcile-all');
        const text = document.getElementById('text-reconcile');
        const icon = document.getElementById('icon-reconcile');
        
        // Save original states
        const originalText = text.textContent;

        // Set loading state
        btn.disabled = true;
        text.textContent = 'Reconciling...';
        icon.style.animation = 'spin 1.5s linear infinite';
        
        try {
            const res = await API.reconcileAll();
            
            // Reload all aircraft and flights immediately
            await Promise.all([
                Flights.loadAircraft(),
                Flights.loadFlights()
            ]);
            
            // Check count of successfully reconciled flights and stuck aircraft
            const totalFlights = res.total_checked || 0;
            const updatedFlights = res.results ? res.results.filter(r => r.status === 'success' && r.flight_id).length : 0;
            const totalAircraft = res.total_aircraft_checked || 0;
            const updatedAircraft = res.results ? res.results.filter(r => r.status === 'success' && r.aircraft_id).length : 0;
            
            let msg = `Reconciliation completed.`;
            if (totalAircraft > 0) {
                msg += ` Checked ${totalFlights} flight(s) (updated ${updatedFlights}), and ${totalAircraft} stuck aircraft (grounded ${updatedAircraft}).`;
            } else {
                msg += ` Checked ${totalFlights} flight(s), updated ${updatedFlights} flight(s).`;
            }
            
            Utils.toast(msg, 'success');
        } catch (err) {
            console.error('Reconciliation failed:', err);
            Utils.toast(`Reconciliation failed: ${err.message || err}`, 'error');
        } finally {
            // Restore button state
            btn.disabled = false;
            text.textContent = originalText;
            icon.style.animation = '';
        }
    }
};

// Boot
document.addEventListener('DOMContentLoaded', () => App.init());
