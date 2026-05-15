/**
 * Planey - API Client
 * REST API wrapper and WebSocket manager
 */

const API = {
    baseUrl: '/api',
    ws: null,
    wsListeners: [],
    wsReconnectTimer: null,

    // ── REST Helpers ──
    async _fetch(path, opts = {}) {
        const url = `${this.baseUrl}${path}`;
        const resp = await fetch(url, {
            headers: { 'Content-Type': 'application/json', ...opts.headers },
            ...opts
        });
        if (!resp.ok) {
            const err = await resp.json().catch(() => ({ detail: resp.statusText }));
            throw new Error(err.detail || `API error ${resp.status}`);
        }
        if (resp.status === 204) return null;
        return resp.json();
    },

    get(path) { return this._fetch(path); },
    post(path, body) { return this._fetch(path, { method: 'POST', body: JSON.stringify(body) }); },
    put(path, body) { return this._fetch(path, { method: 'PUT', body: JSON.stringify(body) }); },
    del(path) { return this._fetch(path, { method: 'DELETE' }); },

    // ── Aircraft ──
    getAircraft(activeOnly = true) { return this.get(`/aircraft?active_only=${activeOnly}`); },
    addAircraft(data) { return this.post('/aircraft', data); },
    updateAircraft(id, data) { return this.put(`/aircraft/${id}`, data); },
    deleteAircraft(id) { return this.del(`/aircraft/${id}`); },
    lookupAircraft(params) {
        const q = new URLSearchParams(params).toString();
        return this.post(`/aircraft/lookup?${q}`);
    },
    syncAircraftFA(id) { return this.post(`/aircraft/${id}/sync_fa`); },
    pollAircraft(id) { return this.post(`/aircraft/${id}/poll`); },

    // ── Flights ──
    getFlights(params = {}) {
        const q = new URLSearchParams(params).toString();
        return this.get(`/flights?${q}`);
    },
    getActiveFlights() { return this.get('/flights/active'); },
    addFlight(data) { return this.post('/flights', data); },
    getFlight(id) { return this.get(`/flights/${id}`); },
    getFlightPositions(id) { return this.get(`/flights/${id}/positions`); },
    updateFlight(id, data) { return this.put(`/flights/${id}`, data); },
    deleteFlight(id) { return this.del(`/flights/${id}`); },

    // ── Positions ──
    getLatestPositions() { return this.get('/positions/latest'); },
    getPositionHistory(aircraftId, hours = 24) { return this.get(`/positions/${aircraftId}/history?hours=${hours}`); },

    // ── Stats & Settings ──
    getStats() { return this.get('/stats'); },
    getHealth() { return this.get('/health'); },
    getSettings() { return this.get('/settings'); },
    updateSettings(settings) { return this.post('/settings', { settings }); },

    // ── WebSocket ──
    connectWS() {
        if (this.ws && this.ws.readyState === WebSocket.OPEN) return;

        const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
        const url = `${proto}//${location.host}/ws`;

        try {
            this.ws = new WebSocket(url);

            this.ws.onopen = () => {
                console.log('[WS] Connected');
                document.getElementById('ws-dot').className = 'status-dot connected';
                document.getElementById('ws-label').textContent = 'Live';
                if (this.wsReconnectTimer) { clearTimeout(this.wsReconnectTimer); this.wsReconnectTimer = null; }
            };

            this.ws.onmessage = (e) => {
                try {
                    const msg = JSON.parse(e.data);
                    this.wsListeners.forEach(fn => fn(msg));
                } catch (err) { console.warn('[WS] Parse error:', err); }
            };

            this.ws.onclose = () => {
                console.log('[WS] Disconnected');
                document.getElementById('ws-dot').className = 'status-dot disconnected';
                document.getElementById('ws-label').textContent = 'Disconnected';
                this._scheduleReconnect();
            };

            this.ws.onerror = (err) => {
                console.error('[WS] Error:', err);
                this.ws.close();
            };
        } catch (err) {
            console.error('[WS] Connection failed:', err);
            this._scheduleReconnect();
        }
    },

    _scheduleReconnect() {
        if (!this.wsReconnectTimer) {
            this.wsReconnectTimer = setTimeout(() => {
                this.wsReconnectTimer = null;
                this.connectWS();
            }, 5000);
        }
    },

    onWS(fn) { this.wsListeners.push(fn); },
    offWS(fn) { this.wsListeners = this.wsListeners.filter(f => f !== fn); }
};
