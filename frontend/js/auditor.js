/**
 * Planey - Data Auditor
 * Flight data cleansing: search, filter, merge, edit, delete.
 */

const TelemetryAuditor = {
    selectedFlightId: null,
    flights: [],
    currentFlight: null,
    currentPositions: [],
    flightCategory: 'plane',
    sortField: 'timestamp',
    sortOrder: 'desc',
    activeTab: 'details',
    selectedPositionIds: new Set(),
    anomalyFilter: false,
    sourceFilter: '',
    statusFilter: 'all',
    aircraftFilter: '',
    dateFilter: 'all',

    async open(defaultFlightId = null) {
        document.getElementById('modal-telemetry-auditor').style.display = 'flex';
        this.initListeners();
        await Promise.all([
            this.loadAircraftList(),
            this.loadFlights(),
        ]);
        if (defaultFlightId) this.selectFlight(defaultFlightId);
    },

    close() {
        document.getElementById('modal-telemetry-auditor').style.display = 'none';
        this.selectedFlightId = null;
        this.currentFlight = null;
        this.currentPositions = [];
        this.selectedPositionIds.clear();
        this.resetWorkspace();
        if (window.Flights) {
            window.Flights.loadAircraft();
            window.Flights.loadFlights();
        }
    },

    resetWorkspace() {
        document.getElementById('auditor-workspace').style.display = 'none';
        document.getElementById('auditor-main-empty').style.display = 'flex';
        const container = document.querySelector('.auditor-container');
        if (container) container.classList.remove('flight-selected');
        this.selectedFlightId = null;
        this.filterAndRenderFlights();
    },

    initListeners() {
        // Search
        const searchInput = document.getElementById('auditor-flight-search');
        searchInput.oninput = () => {
            this.searchQuery = searchInput.value;
            this.filterAndRenderFlights();
        };
        this.searchQuery = searchInput.value || '';

        // Status filter chips
        document.querySelectorAll('.auditor-status-chip[data-status]').forEach(btn => {
            btn.onclick = (e) => {
                document.querySelectorAll('.auditor-status-chip[data-status]').forEach(b => b.classList.remove('active'));
                e.currentTarget.classList.add('active');
                this.statusFilter = e.currentTarget.dataset.status;
                this.filterAndRenderFlights();
            };
        });

        // Aircraft filter
        document.getElementById('auditor-aircraft-filter').onchange = (e) => {
            this.aircraftFilter = e.target.value;
            this.filterAndRenderFlights();
        };

        // Date filter
        document.getElementById('auditor-date-filter').onchange = (e) => {
            this.dateFilter = e.target.value;
            this.filterAndRenderFlights();
        };

        // Back button (mobile)
        document.getElementById('btn-back-to-list').onclick = () => this.resetWorkspace();

        // Workspace action buttons
        document.getElementById('btn-save-flight-audit').onclick = (e) => {
            e.preventDefault();
            this.saveFlightDetails();
        };
        document.getElementById('btn-merge-flight-audit').onclick = () => this.mergeIntoFlight();
        document.getElementById('btn-delete-flight-audit').onclick = () => this.deleteFlight();

        // Tab switching
        document.querySelectorAll('.auditor-tab').forEach(tab => {
            tab.onclick = (e) => this.switchTab(e.currentTarget.dataset.tab);
        });

        // Table header sorting
        const tableHeader = document.querySelector('.auditor-table thead');
        if (tableHeader) {
            tableHeader.onclick = (e) => {
                const th = e.target.closest('th.sortable');
                if (!th) return;
                const field = th.dataset.field;
                if (this.sortField === field) {
                    this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                } else {
                    this.sortField = field;
                    this.sortOrder = 'desc';
                }
                this.renderTelemetryTable(this.currentPositions);
            };
        }

        // Bulk position select
        document.getElementById('select-all-positions').onchange = (e) => {
            const visible = this._getVisiblePositionIds();
            if (e.target.checked) {
                visible.forEach(id => this.selectedPositionIds.add(id));
            } else {
                visible.forEach(id => this.selectedPositionIds.delete(id));
            }
            this.renderTelemetryTable(this.currentPositions);
            this.updateBulkDeleteBtn();
        };

        // Bulk delete
        document.getElementById('btn-delete-selected').onclick = () => this.bulkDeletePositions();

        // Anomaly filter
        document.getElementById('btn-filter-anomalies').onclick = (e) => {
            this.anomalyFilter = !this.anomalyFilter;
            e.currentTarget.classList.toggle('active', this.anomalyFilter);
            this.renderTelemetryTable(this.currentPositions);
        };

        // Source filter
        document.getElementById('positions-source-filter').onchange = (e) => {
            this.sourceFilter = e.target.value;
            this.renderTelemetryTable(this.currentPositions);
        };
    },

    switchTab(tab) {
        this.activeTab = tab;
        document.querySelectorAll('.auditor-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === tab));
        document.querySelectorAll('.auditor-tab-content').forEach(c => {
            const isActive = c.id === `auditor-tab-${tab}`;
            c.classList.toggle('active', isActive);
        });
    },

    // ── Data Loading ──

    async loadFlights() {
        try {
            const list = await API.getFlights({ limit: 200 });
            this.flights = list || [];
            this.populateAircraftFilter();
            this.filterAndRenderFlights();
        } catch (err) {
            console.error('Failed to load flights:', err);
            Utils.toast('Failed to load flights', 'error');
        }
    },

    async loadAircraftList() {
        try {
            const list = await API.getAircraft(false);
            const select = document.getElementById('audit-aircraft');
            if (select) {
                select.innerHTML = '<option value="" disabled selected>Select Aircraft</option>';
                list.forEach(ac => {
                    const icon = ac.category === 'helicopter' ? '🚁' : '✈';
                    select.innerHTML += `<option value="${ac.id}">${icon} ${ac.tail_number || 'N/A'} (${ac.type || 'Unknown'})</option>`;
                });
            }
        } catch (err) {
            console.error('Failed to load aircraft list:', err);
        }
    },

    populateAircraftFilter() {
        const select = document.getElementById('auditor-aircraft-filter');
        const seen = new Map();
        this.flights.forEach(f => {
            if (f.tail_number && !seen.has(String(f.aircraft_id))) {
                seen.set(String(f.aircraft_id), f.tail_number);
            }
        });
        const currentVal = select.value;
        select.innerHTML = '<option value="">All Aircraft</option>';
        seen.forEach((tail, id) => {
            select.innerHTML += `<option value="${id}">${tail}</option>`;
        });
        if (currentVal) select.value = currentVal;
    },

    // ── Flight List Rendering ──

    filterAndRenderFlights() {
        const q = (this.searchQuery || '').toLowerCase().trim();
        const container = document.getElementById('auditor-flights-list');
        container.innerHTML = '';

        let filtered = this.flights.filter(f => {
            if (q) {
                const match =
                    (f.flight_number && f.flight_number.toLowerCase().includes(q)) ||
                    (f.callsign && f.callsign.toLowerCase().includes(q)) ||
                    (f.departure_iata && f.departure_iata.toLowerCase().includes(q)) ||
                    (f.arrival_iata && f.arrival_iata.toLowerCase().includes(q)) ||
                    (f.tail_number && f.tail_number.toLowerCase().includes(q)) ||
                    (f.departure_name && f.departure_name.toLowerCase().includes(q)) ||
                    (f.arrival_name && f.arrival_name.toLowerCase().includes(q));
                if (!match) return false;
            }
            if (this.statusFilter !== 'all' && f.status !== this.statusFilter) return false;
            if (this.aircraftFilter && String(f.aircraft_id) !== this.aircraftFilter) return false;
            if (this.dateFilter && this.dateFilter !== 'all') {
                const days = parseInt(this.dateFilter);
                const cutoff = new Date(Date.now() - days * 86400000);
                const ref = f.actual_departure || f.scheduled_departure || f.created_at;
                if (!ref || new Date(ref) < cutoff) return false;
            }
            return true;
        });

        // Sort: active first, then scheduled, then by created_at desc
        const order = { active: 0, scheduled: 1, completed: 2, landed: 2 };
        filtered.sort((a, b) => {
            const diff = (order[a.status] ?? 3) - (order[b.status] ?? 3);
            if (diff !== 0) return diff;
            return new Date(b.created_at) - new Date(a.created_at);
        });

        const countEl = document.getElementById('auditor-flight-count');
        if (countEl) countEl.textContent = `${filtered.length} flight${filtered.length !== 1 ? 's' : ''}`;

        if (filtered.length === 0) {
            container.innerHTML = '<div class="auditor-empty-list">No flights match your filters</div>';
            return;
        }

        filtered.forEach(f => container.appendChild(this.buildFlightCard(f)));
    },

    buildFlightCard(f) {
        const el = document.createElement('div');
        el.className = `auditor-flight-card${f.id === this.selectedFlightId ? ' active' : ''}`;
        el.dataset.flightId = f.id;

        const colors = { active: 'var(--green)', scheduled: 'var(--accent)', completed: 'var(--text-muted)', landed: 'var(--text-muted)' };
        const statusColor = colors[f.status] || 'var(--text-muted)';

        const dep = f.departure_iata || f.departure_icao || '???';
        const arr = f.arrival_iata || f.arrival_icao || '???';
        const label = f.flight_number || f.callsign || 'Unknown Flight';
        const tailHtml = f.tail_number ? `<span class="card-tail">${f.tail_number}</span>` : '';

        const depTime = f.actual_departure || f.scheduled_departure;
        const arrTime = f.actual_arrival || f.scheduled_arrival;
        const depStr = depTime ? Utils.formatDateTime(depTime) : '—';
        const arrStr = arrTime ? Utils.formatDateTime(arrTime) : '—';

        const posCount = f.position_count != null ? f.position_count : '—';
        const updatedStr = Utils.timeAgo(f.updated_at);
        const createdStr = Utils.formatDateTime(f.created_at);

        el.innerHTML = `
            <div class="card-header-row">
                <div class="card-id">
                    <span class="card-flight-num">${label}</span>
                    ${tailHtml}
                </div>
                <div class="card-header-right">
                    <span class="card-status-badge" style="color:${statusColor};">${f.status}</span>
                    <button class="card-delete-btn" title="Delete flight" onclick="event.stopPropagation(); TelemetryAuditor.deleteFlightById('${f.id}')">
                        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/></svg>
                    </button>
                </div>
            </div>
            <div class="card-route">${dep} → ${arr}</div>
            <div class="card-times">
                <span class="card-time-item"><span class="card-time-label">Dep</span> ${depStr}</span>
                <span class="card-time-sep">·</span>
                <span class="card-time-item"><span class="card-time-label">Arr</span> ${arrStr}</span>
            </div>
            <div class="card-meta">
                <span class="card-pos-count">${posCount} pts</span>
                <span class="card-meta-sep">·</span>
                <span title="Created ${createdStr}">Updated ${updatedStr}</span>
            </div>
        `;

        el.onclick = () => this.selectFlight(f.id);
        return el;
    },

    // ── Flight Selection & Workspace ──

    async selectFlight(flightId, preserveTab = false) {
        this.selectedFlightId = flightId;
        this.filterAndRenderFlights();

        document.getElementById('auditor-main-empty').style.display = 'none';
        const ws = document.getElementById('auditor-workspace');
        ws.style.display = 'flex';
        document.querySelector('.auditor-container').classList.add('flight-selected');

        if (!preserveTab) {
            this.sortField = 'timestamp';
            this.sortOrder = 'desc';
            this.selectedPositionIds.clear();
            this.anomalyFilter = false;
            this.sourceFilter = '';
            document.getElementById('btn-filter-anomalies').classList.remove('active');
            document.getElementById('positions-source-filter').value = '';
            this.switchTab('details');
        }

        try {
            const [flight, positions] = await Promise.all([
                API.getFlight(flightId),
                API.getFlightPositions(flightId),
            ]);

            this.currentFlight = flight;
            this.currentPositions = positions || [];
            this.flightCategory = (flight.aircraft && flight.aircraft.category) || 'plane';

            this.renderWorkspaceHeader(flight, this.currentPositions.length);
            this.renderFlightForm(flight);
            this.renderTelemetryTable(this.currentPositions);
        } catch (err) {
            console.error('Failed to load flight workspace:', err);
            Utils.toast('Failed to load flight details', 'error');
            this.resetWorkspace();
        }
    },

    renderWorkspaceHeader(flight, posCount) {
        const label = flight.flight_number || flight.callsign || 'Unknown Flight';
        const tailPart = flight.aircraft?.tail_number ? ` · ${flight.aircraft.tail_number}` : '';
        document.getElementById('ws-flight-label').textContent = label + tailPart;

        const dep = flight.departure_iata || flight.departure_icao || flight.departure_name || '???';
        const arr = flight.arrival_iata || flight.arrival_icao || flight.arrival_name || '???';
        document.getElementById('ws-route-label').textContent = `${dep} → ${arr}`;

        document.getElementById('auditor-pos-count-badge').textContent = posCount;

        const statsBar = document.getElementById('ws-stats-bar');
        const s = flight.summary_stats;
        let html = `<span class="ws-stat-chip"><strong>${posCount}</strong> pts</span>`;

        if (s) {
            if (s.distance_nm) html += `<span class="ws-stat-chip"><strong>${Math.round(s.distance_nm)}</strong> NM</span>`;
            if (s.avg_speed_kts) html += `<span class="ws-stat-chip"><strong>${Math.round(s.avg_speed_kts)}</strong> kts avg</span>`;
            if (s.max_altitude_ft) {
                const altLabel = s.max_altitude_ft >= 18000
                    ? `FL${Math.round(s.max_altitude_ft / 100)}`
                    : `${Math.round(s.max_altitude_ft).toLocaleString()} ft`;
                html += `<span class="ws-stat-chip"><strong>${altLabel}</strong></span>`;
            }
        }
        html += `<span class="ws-stat-chip" title="Created ${Utils.formatDateTime(flight.created_at)}">Updated ${Utils.timeAgo(flight.updated_at)}</span>`;
        statsBar.innerHTML = html;
    },

    // ── TZ Helpers ──

    formatISOToLocal(isoString) {
        if (!isoString) return '';
        const d = new Date(isoString);
        if (isNaN(d.getTime())) return '';
        const tz = Utils.getTimezone();
        const parts = new Intl.DateTimeFormat('en-CA', {
            timeZone: tz,
            year: 'numeric', month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', hour12: false
        }).formatToParts(d).reduce((acc, p) => { acc[p.type] = p.value; return acc; }, {});
        const h = parts.hour === '24' ? '00' : parts.hour;
        return `${parts.year}-${parts.month}-${parts.day}T${h}:${parts.minute}`;
    },

    _localToUTC(localStr) {
        const asUTC = new Date(localStr + 'Z').getTime();
        const inTz = new Date(asUTC).toLocaleString('sv-SE', { timeZone: Utils.getTimezone() }).replace(' ', 'T');
        const offsetMs = asUTC - new Date(inTz + 'Z').getTime();
        return new Date(asUTC + offsetMs).toISOString();
    },

    _tzAbbr() {
        try {
            return new Intl.DateTimeFormat('en-US', { timeZone: Utils.getTimezone(), timeZoneName: 'short' })
                .formatToParts(new Date())
                .find(p => p.type === 'timeZoneName')?.value || Utils.getTimezone();
        } catch { return Utils.getTimezone(); }
    },

    // ── Flight Form ──

    renderFlightForm(f) {
        document.getElementById('audit-aircraft').value = f.aircraft_id || '';
        document.getElementById('audit-flight-number').value = f.flight_number || '';
        document.getElementById('audit-callsign').value = f.callsign || '';
        document.getElementById('audit-status').value = f.status || 'scheduled';
        document.getElementById('audit-dep-iata').value = f.departure_iata || '';
        document.getElementById('audit-dep-icao').value = f.departure_icao || '';
        document.getElementById('audit-dep-name').value = f.departure_name || '';
        document.getElementById('audit-dep-lat').value = f.departure_lat != null ? f.departure_lat : '';
        document.getElementById('audit-dep-lon').value = f.departure_lon != null ? f.departure_lon : '';
        document.getElementById('audit-arr-iata').value = f.arrival_iata || '';
        document.getElementById('audit-arr-icao').value = f.arrival_icao || '';
        document.getElementById('audit-arr-name').value = f.arrival_name || '';
        document.getElementById('audit-arr-lat').value = f.arrival_lat != null ? f.arrival_lat : '';
        document.getElementById('audit-arr-lon').value = f.arrival_lon != null ? f.arrival_lon : '';
        document.getElementById('audit-sched-dep').value = this.formatISOToLocal(f.scheduled_departure);
        document.getElementById('audit-sched-arr').value = this.formatISOToLocal(f.scheduled_arrival);
        document.getElementById('audit-act-dep').value = this.formatISOToLocal(f.actual_departure);
        document.getElementById('audit-act-arr').value = this.formatISOToLocal(f.actual_arrival);
        document.getElementById('audit-route').value = f.expected_route || '';

        // TZ labels
        const tzLabel = this._tzAbbr();
        [1, 2, 3, 4].forEach(n => {
            const el = document.getElementById(`audit-tz-label-${n}`);
            if (el) el.textContent = tzLabel;
        });
        const colLabel = document.getElementById('auditor-tz-col-label');
        if (colLabel) colLabel.textContent = tzLabel;

        // Stats section
        const statsEl = document.getElementById('flight-audit-stats-summary');
        if (f.summary_stats) {
            const s = f.summary_stats;
            statsEl.innerHTML = [
                s.distance_nm != null ? `<div class="audit-stat-item"><span class="audit-stat-label">Distance</span><span class="audit-stat-value">${s.distance_nm.toFixed(1)} <small>NM</small></span></div>` : '',
                s.avg_speed_kts != null ? `<div class="audit-stat-item"><span class="audit-stat-label">Avg Speed</span><span class="audit-stat-value">${Math.round(s.avg_speed_kts)} <small>kts</small></span></div>` : '',
                s.max_speed_kts != null ? `<div class="audit-stat-item"><span class="audit-stat-label">Max Speed</span><span class="audit-stat-value">${Math.round(s.max_speed_kts)} <small>kts</small></span></div>` : '',
                s.max_altitude_ft != null ? `<div class="audit-stat-item"><span class="audit-stat-label">Max Altitude</span><span class="audit-stat-value">${Math.round(s.max_altitude_ft).toLocaleString()} <small>ft</small></span></div>` : '',
                f.created_at ? `<div class="audit-stat-item"><span class="audit-stat-label">Created</span><span class="audit-stat-value small">${Utils.formatDateTime(f.created_at)}</span></div>` : '',
                f.updated_at ? `<div class="audit-stat-item"><span class="audit-stat-label">Updated</span><span class="audit-stat-value small">${Utils.formatDateTime(f.updated_at)}</span></div>` : '',
            ].join('');
        } else {
            statsEl.innerHTML = '<p style="color: var(--text-muted); font-size: 12px; margin: 0;">No stats available yet — needs telemetry points.</p>';
        }
    },

    async saveFlightDetails() {
        if (!this.selectedFlightId) return;

        const parseCoord = id => { const v = document.getElementById(id).value; return v === '' ? null : parseFloat(v); };
        const parseTime = id => { const v = document.getElementById(id).value; return v === '' ? null : this._localToUTC(v); };
        const aircraftVal = document.getElementById('audit-aircraft').value;

        const data = {
            aircraft_id: aircraftVal ? aircraftVal : null,
            flight_number: document.getElementById('audit-flight-number').value || null,
            callsign: document.getElementById('audit-callsign').value || null,
            status: document.getElementById('audit-status').value,
            departure_iata: document.getElementById('audit-dep-iata').value || null,
            departure_icao: document.getElementById('audit-dep-icao').value || null,
            departure_name: document.getElementById('audit-dep-name').value || null,
            departure_lat: parseCoord('audit-dep-lat'),
            departure_lon: parseCoord('audit-dep-lon'),
            arrival_iata: document.getElementById('audit-arr-iata').value || null,
            arrival_icao: document.getElementById('audit-arr-icao').value || null,
            arrival_name: document.getElementById('audit-arr-name').value || null,
            arrival_lat: parseCoord('audit-arr-lat'),
            arrival_lon: parseCoord('audit-arr-lon'),
            scheduled_departure: parseTime('audit-sched-dep'),
            scheduled_arrival: parseTime('audit-sched-arr'),
            actual_departure: parseTime('audit-act-dep'),
            actual_arrival: parseTime('audit-act-arr'),
            expected_route: document.getElementById('audit-route').value || null,
        };

        try {
            await API.updateFlight(this.selectedFlightId, data);
            Utils.toast('Flight details saved.', 'success');
            await this.loadFlights();
            this.selectFlight(this.selectedFlightId, true);
        } catch (err) {
            console.error('Failed to save flight details:', err);
            Utils.toast(`Save failed: ${err.message}`, 'error');
        }
    },

    // ── Delete Flight ──

    async deleteFlight() {
        if (!this.selectedFlightId || !this.currentFlight) return;
        await this.deleteFlightById(this.selectedFlightId);
    },

    async deleteFlightById(flightId) {
        const flight = this.flights.find(f => f.id === flightId) || (this.selectedFlightId === flightId ? this.currentFlight : null);
        const label = flight ? (flight.flight_number || flight.callsign || 'this flight') : 'this flight';
        const posCount = (flightId === this.selectedFlightId) ? this.currentPositions.length : (flight?.position_count || 0);
        const posMsg = posCount > 0 ? ` and its ${posCount} position record${posCount !== 1 ? 's' : ''}` : '';

        if (!confirm(`Permanently delete "${label}"${posMsg}? This cannot be undone.`)) return;

        try {
            await API.deleteFlight(flightId);
            Utils.toast('Flight deleted.', 'success');

            this.flights = this.flights.filter(f => f.id !== flightId);

            if (this.selectedFlightId === flightId) {
                this.currentFlight = null;
                this.currentPositions = [];
                this.selectedPositionIds.clear();
                this.resetWorkspace();
            } else {
                this.filterAndRenderFlights();
            }

            if (window.Flights) window.Flights.loadFlights();
        } catch (err) {
            Utils.toast(`Delete failed: ${err.message}`, 'error');
        }
    },

    // ── Telemetry Table ──

    _getVisiblePositionIds() {
        const anomalies = this.detectAnomalies(this.currentPositions, this.flightCategory);
        return this.currentPositions
            .filter(p => {
                if (this.sourceFilter && (p.source || '').toLowerCase() !== this.sourceFilter) return false;
                if (this.anomalyFilter && !anomalies[p.id]?.row.length) return false;
                return true;
            })
            .map(p => p.id);
    },

    updateBulkDeleteBtn() {
        const count = this.selectedPositionIds.size;
        const btn = document.getElementById('btn-delete-selected');
        const countEl = document.getElementById('selected-count');
        if (btn) btn.disabled = count === 0;
        if (countEl) countEl.textContent = count;
    },

    async bulkDeletePositions() {
        const count = this.selectedPositionIds.size;
        if (count === 0) return;
        if (!confirm(`Delete ${count} position report${count !== 1 ? 's' : ''}? This cannot be undone.`)) return;

        try {
            const ids = Array.from(this.selectedPositionIds);
            await Promise.all(ids.map(id => API.deletePosition(id)));
            Utils.toast(`${count} position${count !== 1 ? 's' : ''} deleted.`, 'success');
            this.selectedPositionIds.clear();
            this.updateBulkDeleteBtn();
            this.selectFlight(this.selectedFlightId, true);
        } catch (err) {
            Utils.toast(`Error: ${err.message}`, 'error');
        }
    },

    renderTelemetryTable(positions) {
        const tbody = document.getElementById('auditor-telemetry-tbody');
        tbody.innerHTML = '';

        // Update position count badge
        document.getElementById('auditor-pos-count-badge').textContent = positions.length;

        // Detect anomalies for all positions
        const anomalies = this.detectAnomalies(positions, this.flightCategory);

        // Apply filters
        const filtered = positions.filter(p => {
            if (this.sourceFilter && (p.source || '').toLowerCase() !== this.sourceFilter) return false;
            if (this.anomalyFilter && !anomalies[p.id]?.row.length) return false;
            return true;
        });

        if (filtered.length === 0) {
            const msg = positions.length === 0
                ? 'No position records for this flight.'
                : 'No positions match the current filter.';
            tbody.innerHTML = `<tr><td colspan="12" style="text-align:center; padding:30px; color:var(--text-muted);">${msg}</td></tr>`;
            // Sync select-all
            document.getElementById('select-all-positions').checked = false;
            return;
        }

        // Update sort indicators
        document.querySelectorAll('.auditor-table th.sortable').forEach(th => {
            const iconSpan = th.querySelector('.sort-icon');
            if (iconSpan) {
                if (th.dataset.field === this.sortField) {
                    iconSpan.textContent = this.sortOrder === 'asc' ? ' ▲' : ' ▼';
                    th.style.color = 'var(--accent)';
                } else {
                    iconSpan.textContent = '';
                    th.style.color = '';
                }
            }
        });

        // Sort
        const sorted = [...filtered].sort((a, b) => {
            let va, vb;
            if (this.sortField === 'timestamp') {
                va = new Date(a.timestamp).getTime();
                vb = new Date(b.timestamp).getTime();
            } else if (this.sortField === 'status') {
                va = anomalies[a.id]?.row.length || 0;
                vb = anomalies[b.id]?.row.length || 0;
            } else if (this.sortField === 'source') {
                va = a.source || '';
                vb = b.source || '';
                return this.sortOrder === 'asc' ? va.localeCompare(vb) : vb.localeCompare(va);
            } else {
                va = a[this.sortField] ?? -999999;
                vb = b[this.sortField] ?? -999999;
            }
            if (va < vb) return this.sortOrder === 'asc' ? -1 : 1;
            if (va > vb) return this.sortOrder === 'asc' ? 1 : -1;
            return 0;
        });

        const allVisibleSelected = sorted.every(p => this.selectedPositionIds.has(p.id));
        document.getElementById('select-all-positions').checked = sorted.length > 0 && allVisibleSelected;

        sorted.forEach(p => {
            const tr = document.createElement('tr');
            const pAnomaly = anomalies[p.id] || { fields: {}, row: [] };
            const isSelected = this.selectedPositionIds.has(p.id);

            if (pAnomaly.row.length > 0) tr.className = 'anomaly-row';
            if (isSelected) tr.classList.add('row-selected');

            const getTd = (val, field) => {
                const isAnom = pAnomaly.fields[field];
                return `<td class="${isAnom ? 'anomaly-cell' : ''}" title="${isAnom || ''}">${val != null ? val : 'N/A'}</td>`;
            };

            const timeStr = Utils.formatDateTimeSecs(p.timestamp);
            const statusContent = pAnomaly.row.length > 0
                ? `<span class="anomaly-badge" title="${pAnomaly.row.join(', ')}">⚠ ${pAnomaly.row.join(', ')}</span>`
                : `<span style="color:var(--green); font-size:11px;">✓ Valid</span>`;

            const sourceBadge = p.source
                ? `<span class="source-badge source-${p.source.toLowerCase()}">${p.source}</span>`
                : `<span class="source-badge">N/A</span>`;

            const aglFt = p.agl_ft != null ? Math.round(p.agl_ft).toLocaleString() : (p.ground_elevation_ft == null ? '—' : '0');
            const aglTitle = p.ground_elevation_ft != null
                ? `Terrain: ${Math.round(p.ground_elevation_ft).toLocaleString()} ft MSL`
                : 'Terrain elevation not available';

            tr.innerHTML = `
                <td style="padding: 7px 10px; text-align:center;">
                    <input type="checkbox" class="row-checkbox" ${isSelected ? 'checked' : ''} onclick="event.stopPropagation(); TelemetryAuditor._togglePosition(${p.id}, this)">
                </td>
                <td style="white-space:nowrap; font-size:11px;">${timeStr}</td>
                ${getTd(p.latitude.toFixed(5), 'latitude')}
                ${getTd(p.longitude.toFixed(5), 'longitude')}
                ${getTd(p.altitude_ft ? Math.round(p.altitude_ft).toLocaleString() : 0, 'altitude_ft')}
                <td title="${aglTitle}" style="color:${p.ground_elevation_ft == null ? 'var(--text-muted)' : ''}">${aglFt}</td>
                ${getTd(p.ground_speed_kts ? Math.round(p.ground_speed_kts) : 0, 'ground_speed_kts')}
                <td>${p.heading != null ? Math.round(p.heading) : 'N/A'}</td>
                ${getTd(p.vertical_rate_fpm ? Math.round(p.vertical_rate_fpm).toLocaleString() : 0, 'vertical_rate_fpm')}
                <td>${sourceBadge}</td>
                <td>${statusContent}</td>
                <td style="text-align:right; white-space:nowrap;">
                    <button class="btn-ghost" style="padding:2px 6px; font-size:11px; margin-right:3px;" onclick="TelemetryAuditor.editPosition(${p.id})">Edit</button>
                    <button class="btn-ghost" style="padding:2px 6px; font-size:11px; margin-right:3px;" onclick="TelemetryAuditor.reassignPosition(${p.id})">Move</button>
                    <button class="btn-ghost" style="padding:2px 6px; font-size:11px; color:var(--red);" onclick="TelemetryAuditor.deletePosition(${p.id})">Del</button>
                </td>
            `;

            tbody.appendChild(tr);
        });
    },

    _togglePosition(posId, checkbox) {
        if (checkbox.checked) {
            this.selectedPositionIds.add(posId);
        } else {
            this.selectedPositionIds.delete(posId);
        }
        // Update row highlight
        const row = checkbox.closest('tr');
        if (row) row.classList.toggle('row-selected', checkbox.checked);
        // Sync select-all
        const visibleIds = this._getVisiblePositionIds();
        const allChecked = visibleIds.length > 0 && visibleIds.every(id => this.selectedPositionIds.has(id));
        document.getElementById('select-all-positions').checked = allChecked;
        this.updateBulkDeleteBtn();
    },

    // ── Anomaly Detection ──

    detectAnomalies(positions, flightCategory) {
        const anomalies = {};
        const sorted = [...positions].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

        for (let i = 0; i < sorted.length; i++) {
            const curr = sorted[i];
            const prev = i > 0 ? sorted[i - 1] : null;
            anomalies[curr.id] = { fields: {}, row: [] };
            const isHeli = flightCategory === 'helicopter';
            const speedLimit = isHeli ? 180 : 600;

            if (curr.ground_speed_kts > speedLimit) {
                anomalies[curr.id].fields.ground_speed_kts = `Speed exceeds limit (${curr.ground_speed_kts} kts)`;
                anomalies[curr.id].row.push('Extreme Speed');
            }
            const maxAlt = isHeli ? 12000 : 45000;
            if (curr.altitude_ft > maxAlt) {
                anomalies[curr.id].fields.altitude_ft = `Altitude exceeds ceiling (${curr.altitude_ft} ft)`;
                anomalies[curr.id].row.push('Extreme Altitude');
            }
            if (curr.vertical_rate_fpm && Math.abs(curr.vertical_rate_fpm) > 8000) {
                anomalies[curr.id].fields.vertical_rate_fpm = `Improbable vertical rate (${curr.vertical_rate_fpm} fpm)`;
                anomalies[curr.id].row.push('Extreme V-Rate');
            }
            if (prev) {
                const timeDiffSec = (new Date(curr.timestamp) - new Date(prev.timestamp)) / 1000;
                if (timeDiffSec > 0) {
                    const distNM = this.haversineDistance(prev.latitude, prev.longitude, curr.latitude, curr.longitude);
                    const calcSpeed = distNM / (timeDiffSec / 3600);
                    if (calcSpeed > speedLimit + 100 && distNM > 1) {
                        anomalies[curr.id].fields.latitude = `Impossible displacement: ${Math.round(calcSpeed)} kts`;
                        anomalies[curr.id].fields.longitude = `Impossible displacement: ${Math.round(calcSpeed)} kts`;
                        anomalies[curr.id].row.push('Spatial Jump');
                    }
                    const altDiff = Math.abs(curr.altitude_ft - prev.altitude_ft);
                    const calcVRate = altDiff / (timeDiffSec / 60);
                    if (calcVRate > 12000 && altDiff > 1000) {
                        anomalies[curr.id].fields.altitude_ft = `Impossible altitude change: ${Math.round(calcVRate)} fpm`;
                        anomalies[curr.id].row.push('Altitude Spike');
                    }
                }
            }
        }
        return anomalies;
    },

    haversineDistance(lat1, lon1, lat2, lon2) {
        if (lat1 === lat2 && lon1 === lon2) return 0;
        const R = 3440.065;
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) ** 2 +
            Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.sin(dLon / 2) ** 2;
        return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
    },

    // ── Position Actions ──

    async editPosition(posId) {
        const p = this.currentPositions.find(x => x.id === posId);
        if (!p) return;

        const overlay = document.createElement('div');
        overlay.className = 'popover-overlay';
        overlay.id = 'edit-pos-popover';
        overlay.innerHTML = `
            <div class="modal" style="max-width: 440px;">
                <h3 style="margin-bottom:16px; font-weight:600; display:flex; justify-content:space-between; align-items:center;">
                    <span>Edit Position #${p.id}</span>
                    <span class="source-badge source-${(p.source || 'na').toLowerCase()}">${p.source || 'N/A'}</span>
                </h3>
                <form id="edit-pos-form" style="display:flex; flex-direction:column; gap:12px;">
                    <div style="display:flex; gap:10px;">
                        <div class="form-group" style="flex:1;">
                            <label style="font-size:11px;">Latitude</label>
                            <input type="number" id="edit-lat" class="input-field" step="any" value="${p.latitude}" required>
                        </div>
                        <div class="form-group" style="flex:1;">
                            <label style="font-size:11px;">Longitude</label>
                            <input type="number" id="edit-lon" class="input-field" step="any" value="${p.longitude}" required>
                        </div>
                    </div>
                    <div style="display:flex; gap:10px;">
                        <div class="form-group" style="flex:1;">
                            <label style="font-size:11px;">Altitude (ft)</label>
                            <input type="number" id="edit-alt" class="input-field" value="${p.altitude_ft || ''}">
                        </div>
                        <div class="form-group" style="flex:1;">
                            <label style="font-size:11px;">Speed (kts)</label>
                            <input type="number" id="edit-speed" class="input-field" value="${p.ground_speed_kts || ''}">
                        </div>
                    </div>
                    <div style="display:flex; gap:10px;">
                        <div class="form-group" style="flex:1;">
                            <label style="font-size:11px;">Heading (°)</label>
                            <input type="number" id="edit-heading" class="input-field" value="${p.heading != null ? p.heading : ''}">
                        </div>
                        <div class="form-group" style="flex:1;">
                            <label style="font-size:11px;">Vertical Rate (fpm)</label>
                            <input type="number" id="edit-vrate" class="input-field" value="${p.vertical_rate_fpm || ''}">
                        </div>
                    </div>
                    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:6px;">
                        <button type="button" class="btn-ghost" onclick="document.getElementById('edit-pos-popover').remove()">Cancel</button>
                        <button type="submit" class="btn-primary">Save Changes</button>
                    </div>
                </form>
            </div>
        `;
        document.body.appendChild(overlay);

        document.getElementById('edit-pos-form').onsubmit = async (e) => {
            e.preventDefault();
            const parseNum = v => v === '' ? null : parseFloat(v);
            try {
                await API.updatePosition(posId, {
                    latitude: parseFloat(document.getElementById('edit-lat').value),
                    longitude: parseFloat(document.getElementById('edit-lon').value),
                    altitude_ft: parseNum(document.getElementById('edit-alt').value),
                    ground_speed_kts: parseNum(document.getElementById('edit-speed').value),
                    heading: parseNum(document.getElementById('edit-heading').value),
                    vertical_rate_fpm: parseNum(document.getElementById('edit-vrate').value),
                });
                Utils.toast('Position updated.', 'success');
                overlay.remove();
                this.selectFlight(this.selectedFlightId, true);
            } catch (err) {
                Utils.toast(`Error: ${err.message}`, 'error');
            }
        };
    },

    async deletePosition(posId) {
        if (!confirm('Delete this position report? This permanently removes the data point and recalculates flight stats.')) return;
        try {
            await API.deletePosition(posId);
            Utils.toast('Position deleted.', 'success');
            this.selectedPositionIds.delete(posId);
            this.updateBulkDeleteBtn();
            this.selectFlight(this.selectedFlightId, true);
        } catch (err) {
            Utils.toast(`Error: ${err.message}`, 'error');
        }
    },

    // ── Merge Flight ──

    async mergeIntoFlight() {
        if (!this.selectedFlightId || !this.currentFlight) return;

        let candidates = [];
        try {
            const all = await API.getFlights({ limit: 200 });
            candidates = all
                .filter(f => f.aircraft_id === this.currentFlight.aircraft_id && f.id !== this.selectedFlightId)
                .sort((a, b) => new Date(b.created_at) - new Date(a.created_at));
        } catch (err) {
            Utils.toast('Failed to load flights for merge', 'error');
            return;
        }

        if (candidates.length === 0) {
            Utils.toast('No other flights found for this aircraft.', 'info');
            return;
        }

        const optionsHtml = candidates.map(f => {
            const route = `${f.departure_iata || '???'} → ${f.arrival_iata || '???'}`;
            const pts = f.position_count != null ? `${f.position_count} pts` : '? pts';
            const label = `${f.flight_number || f.callsign || 'Unknown'} (${route}) · ${pts} · ${f.status} · ${Utils.formatDateShort(f.created_at)}`;
            return `<option value="${f.id}">${label}</option>`;
        }).join('');

        const currentLabel = `${this.currentFlight.flight_number || this.currentFlight.callsign || 'this flight'} (${this.currentFlight.departure_iata || '???'} → ${this.currentFlight.arrival_iata || '???'})`;

        const overlay = document.createElement('div');
        overlay.className = 'popover-overlay';
        overlay.id = 'merge-flight-popover';
        overlay.innerHTML = `
            <div class="modal" style="max-width: 500px;">
                <h3 style="margin-bottom:12px; font-weight:600;">Merge Flight</h3>
                <p style="font-size:12px; color:var(--text-secondary); margin-bottom:16px; line-height:1.5;">
                    All <strong>${this.currentPositions.length} positions</strong> from <strong style="color:var(--text-primary);">${currentLabel}</strong> will be moved into the selected target, then this flight will be deleted.
                </p>
                <form id="merge-flight-form" style="display:flex; flex-direction:column; gap:16px;">
                    <div class="form-group">
                        <label style="font-size:11px;">Merge positions INTO</label>
                        <select id="merge-target-select" class="input-field" style="height:auto; padding:8px;">
                            ${optionsHtml}
                        </select>
                    </div>
                    <div style="padding:10px 12px; background:rgba(255,160,0,0.08); border:1px solid rgba(255,160,0,0.3); border-radius:6px; font-size:11px; color:var(--text-secondary); line-height:1.5;">
                        This action is <strong style="color:var(--text-primary);">irreversible</strong>. The current flight record will be permanently deleted after positions are transferred.
                    </div>
                    <div style="display:flex; justify-content:flex-end; gap:8px;">
                        <button type="button" class="btn-ghost" onclick="document.getElementById('merge-flight-popover').remove()">Cancel</button>
                        <button type="submit" class="btn-primary" style="background:var(--accent);">Merge &amp; Delete</button>
                    </div>
                </form>
            </div>
        `;
        document.body.appendChild(overlay);

        document.getElementById('merge-flight-form').onsubmit = async (e) => {
            e.preventDefault();
            const targetId = document.getElementById('merge-target-select').value;
            if (!targetId) return;
            try {
                await API.mergeFlights(targetId, this.selectedFlightId);
                Utils.toast('Flights merged. Positions transferred.', 'success');
                overlay.remove();
                await this.loadFlights();
                this.selectFlight(targetId);
            } catch (err) {
                Utils.toast(`Merge failed: ${err.message}`, 'error');
            }
        };
    },

    // ── Reassign Position ──

    async reassignPosition(posId) {
        const p = this.currentPositions.find(x => x.id === posId);
        if (!p) return;

        let targetFlights = [];
        try {
            const all = await API.getFlights({ limit: 200 });
            targetFlights = all.filter(f => f.aircraft_id === p.aircraft_id);
        } catch (err) {
            console.warn('Failed to fetch flights for reassignment:', err);
        }

        const optionsHtml = targetFlights.map(f => {
            const route = `${f.departure_iata || '???'} → ${f.arrival_iata || '???'}`;
            const label = `${f.flight_number || f.callsign || 'Unknown'} (${route}) — ${f.status} (${Utils.formatDateShort(f.created_at)})`;
            return `<option value="${f.id}" ${f.id === this.selectedFlightId ? 'selected' : ''}>${label}</option>`;
        }).join('');

        const overlay = document.createElement('div');
        overlay.className = 'popover-overlay';
        overlay.id = 'reassign-pos-popover';
        overlay.innerHTML = `
            <div class="modal" style="max-width: 460px;">
                <h3 style="margin-bottom:12px; font-weight:600;">Move Position #${p.id}</h3>
                <p style="font-size:12px; color:var(--text-secondary); margin-bottom:16px; line-height:1.4;">
                    Reassign this telemetry point to a different flight leg.
                </p>
                <form id="reassign-pos-form" style="display:flex; flex-direction:column; gap:16px;">
                    <div class="form-group">
                        <label style="font-size:11px;">Target Flight</label>
                        <select id="reassign-flight-select" class="input-field" style="height:auto; padding:8px;">
                            ${optionsHtml || '<option value="">No flights found</option>'}
                        </select>
                    </div>
                    <div style="display:flex; justify-content:flex-end; gap:8px;">
                        <button type="button" class="btn-ghost" onclick="document.getElementById('reassign-pos-popover').remove()">Cancel</button>
                        <button type="submit" class="btn-primary" ${targetFlights.length === 0 ? 'disabled' : ''}>Move Position</button>
                    </div>
                </form>
            </div>
        `;
        document.body.appendChild(overlay);

        document.getElementById('reassign-pos-form').onsubmit = async (e) => {
            e.preventDefault();
            const targetFlightId = document.getElementById('reassign-flight-select').value;
            if (!targetFlightId) return;
            try {
                await API.updatePosition(posId, { flight_id: targetFlightId });
                Utils.toast('Position reassigned.', 'success');
                overlay.remove();
                this.selectFlight(this.selectedFlightId, true);
            } catch (err) {
                Utils.toast(`Error: ${err.message}`, 'error');
            }
        };
    },
};

window.TelemetryAuditor = TelemetryAuditor;
