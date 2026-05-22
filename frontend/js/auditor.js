/**
 * Planey - Telemetry Auditor Workspace
 * UI control, data hygiene, and anomaly detection for flight telemetry.
 */

const TelemetryAuditor = {
    selectedFlightId: null,
    flights: [],
    currentFlight: null,
    currentPositions: [],
    flightCategory: 'plane', // plane or helicopter
    sortField: 'timestamp',
    sortOrder: 'desc',

    async open(defaultFlightId = null) {
        document.getElementById('modal-telemetry-auditor').style.display = 'flex';
        this.initListeners();
        await Promise.all([
            this.loadAircraftList(),
            this.loadFlights()
        ]);
        if (defaultFlightId) {
            this.selectFlight(defaultFlightId);
        }
    },

    close() {
        document.getElementById('modal-telemetry-auditor').style.display = 'none';
        this.selectedFlightId = null;
        this.currentFlight = null;
        this.currentPositions = [];
        this.resetWorkspace();
        
        // Refresh main page UI when we close the auditor, in case we modified anything
        if (window.Flights) {
            window.Flights.loadAircraft();
            window.Flights.loadFlights();
        }
    },

    resetWorkspace() {
        document.getElementById('auditor-workspace').style.display = 'none';
        document.getElementById('auditor-main-empty').style.display = 'flex';
        const container = document.querySelector('.auditor-container');
        if (container) {
            container.classList.remove('flight-selected');
        }
    },

    initListeners() {
        // Search and filter listeners
        const searchInput = document.getElementById('auditor-flight-search');
        const filterSelect = document.getElementById('auditor-flight-status-filter');
        
        // Remove existing listeners by replacing elements or just standard setup
        searchInput.oninput = () => this.filterAndRenderFlights();
        filterSelect.onchange = () => this.filterAndRenderFlights();

        // Flight details Save button
        const saveFlightBtn = document.getElementById('btn-save-flight-audit');
        saveFlightBtn.onclick = (e) => {
            e.preventDefault();
            this.saveFlightDetails();
        };

        // Table header sorting click delegation
        const tableHeader = document.querySelector('.auditor-table thead');
        if (tableHeader) {
            tableHeader.onclick = (e) => {
                const th = e.target.closest('th.sortable');
                if (th) {
                    const field = th.dataset.field;
                    if (this.sortField === field) {
                        this.sortOrder = this.sortOrder === 'asc' ? 'desc' : 'asc';
                    } else {
                        this.sortField = field;
                        this.sortOrder = 'desc';
                    }
                    this.renderTelemetryTable(this.currentPositions);
                }
            };
        }
    },

    async loadFlights() {
        try {
            // Fetch the last 100 flights
            const list = await API.getFlights({ limit: 150 });
            this.flights = list || [];
            this.filterAndRenderFlights();
        } catch (err) {
            console.error("Failed to load flights for auditor:", err);
            Utils.toast('Failed to load flights', 'error');
        }
    },

    async loadAircraftList() {
        try {
            const list = await API.getAircraft(false); // get all aircraft
            const select = document.getElementById('audit-aircraft');
            if (select) {
                select.innerHTML = '<option value="" disabled selected>Select Aircraft</option>';
                list.forEach(ac => {
                    const label = ac.category === 'helicopter' ? '🚁' : '✈';
                    select.innerHTML += `<option value="${ac.id}">${label} ${ac.tail_number || 'N/A'} (${ac.type || 'Unknown'})</option>`;
                });
            }
        } catch (err) {
            console.error("Failed to load aircraft list for auditor:", err);
        }
    },

    filterAndRenderFlights() {
        const q = document.getElementById('auditor-flight-search').value.toLowerCase().trim();
        const statusFilter = document.getElementById('auditor-flight-status-filter').value;
        const container = document.getElementById('auditor-flights-list');
        
        container.innerHTML = '';

        const filtered = this.flights.filter(f => {
            const matchesSearch = 
                (f.flight_number && f.flight_number.toLowerCase().includes(q)) ||
                (f.callsign && f.callsign.toLowerCase().includes(q)) ||
                (f.departure_iata && f.departure_iata.toLowerCase().includes(q)) ||
                (f.arrival_iata && f.arrival_iata.toLowerCase().includes(q));
            
            const matchesStatus = statusFilter === 'all' || f.status === statusFilter;
            
            return matchesSearch && matchesStatus;
        });

        if (filtered.length === 0) {
            container.innerHTML = '<div style="padding: 20px; text-align: center; color: var(--text-secondary); font-size: 12px;">No flights match criteria</div>';
            return;
        }

        // Sort: Active first, then by updated_at or scheduled_departure desc
        filtered.sort((a, b) => {
            if (a.status === 'active' && b.status !== 'active') return -1;
            if (b.status === 'active' && a.status !== 'active') return 1;
            return new Date(b.created_at) - new Date(a.created_at);
        });

        filtered.forEach(f => {
            const el = document.createElement('div');
            el.className = `auditor-flight-item ${f.id === this.selectedFlightId ? 'active' : ''}`;
            
            let statusColor = 'var(--text-secondary)';
            if (f.status === 'active') statusColor = 'var(--green)';
            if (f.status === 'scheduled') statusColor = 'var(--accent)';
            if (f.status === 'completed') statusColor = 'var(--text-muted, #7f8c8d)';

            el.innerHTML = `
                <div class="auditor-flight-info-row">
                    <span class="auditor-flight-number">${f.flight_number || f.callsign || 'Unknown Flight'}</span>
                    <span class="auditor-flight-status" style="background: rgba(255, 255, 255, 0.05); color: ${statusColor}; border: 1px solid ${statusColor}44;">${f.status}</span>
                </div>
                <div class="auditor-flight-info-row" style="font-size: 11px; margin-top: 4px;">
                    <span class="auditor-flight-tail">Tail: ${f.tail_number || 'N/A'}</span>
                    <span class="auditor-flight-route">${f.departure_iata || '???'} → ${f.arrival_iata || '???'}</span>
                </div>
            `;

            el.onclick = () => this.selectFlight(f.id);
            container.appendChild(el);
        });
    },

    async selectFlight(flightId) {
        this.selectedFlightId = flightId;
        
        // Highlight active flight item
        document.querySelectorAll('.auditor-flight-item').forEach(el => el.classList.remove('active'));
        this.filterAndRenderFlights(); // Refreshes classes

        document.getElementById('auditor-main-empty').style.display = 'none';
        document.getElementById('auditor-workspace').style.display = 'flex';
        const container = document.querySelector('.auditor-container');
        if (container) {
            container.classList.add('flight-selected');
        }

        this.sortField = 'timestamp';
        this.sortOrder = 'desc';

        try {
            const [flight, positions] = await Promise.all([
                API.getFlight(flightId),
                API.getFlightPositions(flightId)
            ]);

            this.currentFlight = flight;
            this.currentPositions = positions || [];
            
            // Set flight category
            this.flightCategory = (flight.aircraft && flight.aircraft.category) || 'plane';

            this.renderFlightForm(flight);
            this.renderTelemetryTable(positions);
        } catch (err) {
            console.error("Failed to load flight workspace:", err);
            Utils.toast('Failed to load flight details', 'error');
            this.resetWorkspace();
        }
    },

    formatISOToLocal(isoString) {
        if (!isoString) return '';
        const d = new Date(isoString);
        if (isNaN(d.getTime())) return '';
        
        const y = d.getUTCFullYear();
        const m = String(d.getUTCMonth() + 1).padStart(2, '0');
        const day = String(d.getUTCDate()).padStart(2, '0');
        const h = String(d.getUTCHours()).padStart(2, '0');
        const min = String(d.getUTCMinutes()).padStart(2, '0');
        
        return `${y}-${m}-${day}T${h}:${min}`;
    },

    renderFlightForm(f) {
        document.getElementById('audit-aircraft').value = f.aircraft_id || '';
        document.getElementById('audit-flight-number').value = f.flight_number || '';
        document.getElementById('audit-callsign').value = f.callsign || '';
        document.getElementById('audit-status').value = f.status || 'scheduled';
        document.getElementById('audit-dep-iata').value = f.departure_iata || '';
        document.getElementById('audit-dep-icao').value = f.departure_icao || '';
        document.getElementById('audit-dep-name').value = f.departure_name || '';
        document.getElementById('audit-dep-lat').value = f.departure_lat !== null ? f.departure_lat : '';
        document.getElementById('audit-dep-lon').value = f.departure_lon !== null ? f.departure_lon : '';
        document.getElementById('audit-arr-iata').value = f.arrival_iata || '';
        document.getElementById('audit-arr-icao').value = f.arrival_icao || '';
        document.getElementById('audit-arr-name').value = f.arrival_name || '';
        document.getElementById('audit-arr-lat').value = f.arrival_lat !== null ? f.arrival_lat : '';
        document.getElementById('audit-arr-lon').value = f.arrival_lon !== null ? f.arrival_lon : '';
        
        document.getElementById('audit-sched-dep').value = this.formatISOToLocal(f.scheduled_departure);
        document.getElementById('audit-sched-arr').value = this.formatISOToLocal(f.scheduled_arrival);
        document.getElementById('audit-act-dep').value = this.formatISOToLocal(f.actual_departure);
        document.getElementById('audit-act-arr').value = this.formatISOToLocal(f.actual_arrival);
        
        document.getElementById('audit-route').value = f.expected_route || '';

        // Display summary stats
        const statsEl = document.getElementById('flight-audit-stats-summary');
        if (f.summary_stats) {
            const s = f.summary_stats;
            statsEl.innerHTML = `
                <strong>Stats Summary:</strong> 
                Distance: ${s.distance_nm ? s.distance_nm.toFixed(1) : 0} NM | 
                Avg Speed: ${s.avg_speed_kts ? Math.round(s.avg_speed_kts) : 0} kts | 
                Max Speed: ${s.max_speed_kts ? Math.round(s.max_speed_kts) : 0} kts | 
                Max Alt: ${s.max_altitude_ft ? Math.round(s.max_altitude_ft).toLocaleString() : 0} ft
            `;
        } else {
            statsEl.innerHTML = '<strong>Stats Summary:</strong> No stats available (needs telemetry points)';
        }
    },

    async saveFlightDetails() {
        if (!this.selectedFlightId) return;

        const parseCoord = (id) => {
            const val = document.getElementById(id).value;
            return val === '' ? null : parseFloat(val);
        };

        const parseTime = (id) => {
            const val = document.getElementById(id).value;
            return val === '' ? null : new Date(val + 'Z').toISOString(); // Append Z to store as UTC
        };

        const aircraftVal = document.getElementById('audit-aircraft').value;
        const aircraftId = aircraftVal ? parseInt(aircraftVal) : null;

        const data = {
            aircraft_id: aircraftId,
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
            expected_route: document.getElementById('audit-route').value || null
        };

        try {
            const updated = await API.updateFlight(this.selectedFlightId, data);
            Utils.toast('Flight metadata saved and audit logged.', 'success');
            
            // Reload flight lists and current details
            this.loadFlights();
            this.selectFlight(this.selectedFlightId);
        } catch (err) {
            console.error("Failed to save flight details:", err);
            Utils.toast(`Failed to save: ${err.message}`, 'error');
        }
    },

    renderTelemetryTable(positions) {
        const tbody = document.getElementById('auditor-telemetry-tbody');
        const badge = document.getElementById('telemetry-count-badge');
        
        tbody.innerHTML = '';
        badge.textContent = `${positions.length} reports`;

        if (positions.length === 0) {
            tbody.innerHTML = '<tr><td colspan="10" style="text-align: center; padding: 30px; color: var(--text-secondary);">No position logs for this flight.</td></tr>';
            return;
        }

        // Update header sort indicators
        document.querySelectorAll('.auditor-table th.sortable').forEach(th => {
            const field = th.dataset.field;
            const iconSpan = th.querySelector('.sort-icon');
            if (iconSpan) {
                if (field === this.sortField) {
                    iconSpan.textContent = this.sortOrder === 'asc' ? ' ▲' : ' ▼';
                    th.style.color = 'var(--accent)';
                } else {
                    iconSpan.textContent = '';
                    th.style.color = '';
                }
            }
        });

        // Run client-side anomaly checks
        const anomalies = this.detectAnomalies(positions, this.flightCategory);

        // Sort by selected field
        const sorted = [...positions].sort((a, b) => {
            let valA, valB;
            if (this.sortField === 'timestamp') {
                valA = new Date(a.timestamp).getTime();
                valB = new Date(b.timestamp).getTime();
            } else if (this.sortField === 'status') {
                valA = anomalies[a.id]?.row.length || 0;
                valB = anomalies[b.id]?.row.length || 0;
            } else if (this.sortField === 'source') {
                valA = a.source || '';
                valB = b.source || '';
                return this.sortOrder === 'asc' ? valA.localeCompare(valB) : valB.localeCompare(valA);
            } else {
                valA = a[this.sortField];
                valB = b[this.sortField];
                if (valA === null || valA === undefined) valA = -999999;
                if (valB === null || valB === undefined) valB = -999999;
            }

            if (valA < valB) return this.sortOrder === 'asc' ? -1 : 1;
            if (valA > valB) return this.sortOrder === 'asc' ? 1 : -1;
            return 0;
        });

        sorted.forEach(p => {
            const tr = document.createElement('tr');
            const pAnomaly = anomalies[p.id] || { fields: {}, row: [] };
            
            if (pAnomaly.row.length > 0) {
                tr.className = 'anomaly-row';
            }

            const timeStr = new Date(p.timestamp).toISOString().replace('T', ' ').substring(0, 19);

            // Create cells, applying anomaly formatting
            const getTd = (val, fieldName) => {
                const isAnom = pAnomaly.fields[fieldName];
                return `<td class="${isAnom ? 'anomaly-cell' : ''}" title="${isAnom || ''}">${val !== null ? val : 'N/A'}</td>`;
            };

            const statusContent = pAnomaly.row.length > 0 
                ? `<span class="anomaly-badge" title="${pAnomaly.row.map(r => `${r}: ${pAnomaly.fields.ground_speed_kts || pAnomaly.fields.altitude_ft || pAnomaly.fields.latitude || ''}`).join(', ')}">Anomaly: ${pAnomaly.row.join(', ')}</span>`
                : `<span style="color: var(--green); font-weight: 500;">Valid</span>`;

            const sourceBadge = p.source 
                ? `<span class="source-badge source-${p.source.toLowerCase()}">${p.source}</span>` 
                : `<span class="source-badge">N/A</span>`;

            tr.innerHTML = `
                <td>${timeStr}</td>
                ${getTd(p.latitude.toFixed(5), 'latitude')}
                ${getTd(p.longitude.toFixed(5), 'longitude')}
                ${getTd(p.altitude_ft ? p.altitude_ft.toLocaleString() : 0, 'altitude_ft')}
                ${getTd(p.ground_speed_kts ? Math.round(p.ground_speed_kts) : 0, 'ground_speed_kts')}
                <td>${p.heading !== null ? Math.round(p.heading) : 'N/A'}</td>
                ${getTd(p.vertical_rate_fpm ? p.vertical_rate_fpm.toLocaleString() : 0, 'vertical_rate_fpm')}
                <td>${sourceBadge}</td>
                <td>${statusContent}</td>
                <td style="text-align: right; white-space: nowrap;">
                    <button class="btn-ghost" style="padding: 2px 6px; font-size: 11px; margin-right: 4px;" onclick="TelemetryAuditor.editPosition(${p.id})">Edit</button>
                    <button class="btn-ghost" style="padding: 2px 6px; font-size: 11px; margin-right: 4px;" onclick="TelemetryAuditor.reassignPosition(${p.id})">Reassign</button>
                    <button class="btn-ghost delete-icon" style="padding: 2px 6px; font-size: 11px; color: var(--red);" onclick="TelemetryAuditor.deletePosition(${p.id})">Delete</button>
                </td>
            `;

            tbody.appendChild(tr);
        });
    },

    detectAnomalies(positions, flightCategory) {
        const anomalies = {}; // maps position.id -> { fields: { fieldName: description }, row: description }
        
        // Sort positions by timestamp ascending to evaluate sequence
        const sorted = [...positions].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
        
        for (let i = 0; i < sorted.length; i++) {
            const curr = sorted[i];
            const prev = i > 0 ? sorted[i - 1] : null;
            
            anomalies[curr.id] = { fields: {}, row: [] };
            
            // Rule 1: Speed check based on aircraft category
            const isHeli = flightCategory === 'helicopter';
            const speedLimit = isHeli ? 180 : 600; // knots
            if (curr.ground_speed_kts > speedLimit) {
                anomalies[curr.id].fields.ground_speed_kts = `Speed exceeds typical limit for ${flightCategory} (${curr.ground_speed_kts} kts)`;
                anomalies[curr.id].row.push("Extreme Speed");
            }
            
            // Rule 2: Altitude spikes
            const maxAlt = isHeli ? 12000 : 45000;
            if (curr.altitude_ft > maxAlt) {
                anomalies[curr.id].fields.altitude_ft = `Altitude exceeds ceiling (${curr.altitude_ft} ft)`;
                anomalies[curr.id].row.push("Extreme Altitude");
            }
            
            // Rule 3: Vertical Rate Spikes
            if (curr.vertical_rate_fpm && Math.abs(curr.vertical_rate_fpm) > 8000) {
                anomalies[curr.id].fields.vertical_rate_fpm = `Improbable vertical rate (${curr.vertical_rate_fpm} fpm)`;
                anomalies[curr.id].row.push("Extreme V-Rate");
            }
            
            // Rule 4: Sequential distance checks (haversine speed check)
            if (prev) {
                const timeDiffSec = (new Date(curr.timestamp) - new Date(prev.timestamp)) / 1000;
                if (timeDiffSec > 0) {
                    const distNM = this.haversineDistance(prev.latitude, prev.longitude, curr.latitude, curr.longitude);
                    const calcSpeedKts = (distNM / (timeDiffSec / 3600));
                    
                    // If calculated speed between points is physically impossible
                    if (calcSpeedKts > speedLimit + 100 && distNM > 1) {
                        anomalies[curr.id].fields.latitude = `Impossible displacement speed: ${Math.round(calcSpeedKts)} kts`;
                        anomalies[curr.id].fields.longitude = `Impossible displacement speed: ${Math.round(calcSpeedKts)} kts`;
                        anomalies[curr.id].row.push("Spatial Jump");
                    }
                    
                    // If altitude jump is impossible
                    const altDiff = Math.abs(curr.altitude_ft - prev.altitude_ft);
                    const calcVRate = altDiff / (timeDiffSec / 60);
                    if (calcVRate > 12000 && altDiff > 1000) {
                        anomalies[curr.id].fields.altitude_ft = `Impossible altitude rate of change: ${Math.round(calcVRate)} fpm`;
                        anomalies[curr.id].row.push("Altitude Spike");
                    }
                }
            }
        }
        
        return anomalies;
    },

    haversineDistance(lat1, lon1, lat2, lon2) {
        if (lat1 === lat2 && lon1 === lon2) return 0;
        const R = 3440.065; // NM
        const dLat = (lat2 - lat1) * Math.PI / 180;
        const dLon = (lon2 - lon1) * Math.PI / 180;
        const a = Math.sin(dLat / 2) * Math.sin(dLat / 2) +
                  Math.cos(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) *
                  Math.sin(dLon / 2) * Math.sin(dLon / 2);
        const c = 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
        return R * c;
    },

    // --- Telemetry Edit Actions ---
    async editPosition(posId) {
        const p = this.currentPositions.find(x => x.id === posId);
        if (!p) return;

        // Render a nice clean inline edit popover modal
        const overlay = document.createElement('div');
        overlay.className = 'popover-overlay';
        overlay.id = 'edit-pos-popover';

        overlay.innerHTML = `
            <div class="modal">
                <h3 style="margin-bottom: 16px; font-weight: 600; display: flex; justify-content: space-between; align-items: center;">
                    <span>Edit Position Report #${p.id}</span>
                    <span class="source-badge source-${(p.source || 'n/a').toLowerCase()}">${p.source || 'N/A'}</span>
                </h3>
                <form id="edit-pos-form" style="display: flex; flex-direction: column; gap: 12px;">
                    <div style="display: flex; gap: 10px;">
                        <div class="form-group" style="flex: 1;">
                            <label style="font-size: 11px;">Latitude</label>
                            <input type="number" id="edit-lat" class="input-field" step="any" value="${p.latitude}" required>
                        </div>
                        <div class="form-group" style="flex: 1;">
                            <label style="font-size: 11px;">Longitude</label>
                            <input type="number" id="edit-lon" class="input-field" step="any" value="${p.longitude}" required>
                        </div>
                    </div>
                    <div style="display: flex; gap: 10px;">
                        <div class="form-group" style="flex: 1;">
                            <label style="font-size: 11px;">Altitude (ft)</label>
                            <input type="number" id="edit-alt" class="input-field" value="${p.altitude_ft || ''}">
                        </div>
                        <div class="form-group" style="flex: 1;">
                            <label style="font-size: 11px;">Speed (kts)</label>
                            <input type="number" id="edit-speed" class="input-field" value="${p.ground_speed_kts || ''}">
                        </div>
                    </div>
                    <div style="display: flex; gap: 10px;">
                        <div class="form-group" style="flex: 1;">
                            <label style="font-size: 11px;">Heading (°)</label>
                            <input type="number" id="edit-heading" class="input-field" value="${p.heading !== null ? p.heading : ''}">
                        </div>
                        <div class="form-group" style="flex: 1;">
                            <label style="font-size: 11px;">Vertical Rate (fpm)</label>
                            <input type="number" id="edit-vrate" class="input-field" value="${p.vertical_rate_fpm || ''}">
                        </div>
                    </div>
                    <div class="form-group">
                        <label style="font-size: 11px;">Report Source</label>
                        <input type="text" class="input-field" value="${p.source ? p.source.toUpperCase() : 'N/A'}" readonly style="background: rgba(255,255,255,0.02); color: var(--text-secondary); cursor: not-allowed; font-family: var(--mono); font-size: 11px; letter-spacing: 0.5px;">
                    </div>
                    <div style="display: flex; justify-content: flex-end; gap: 8px; margin-top: 10px;">
                        <button type="button" class="btn-ghost" onclick="document.getElementById('edit-pos-popover').remove()">Cancel</button>
                        <button type="submit" class="btn-primary">Save Changes</button>
                    </div>
                </form>
            </div>
        `;

        document.body.appendChild(overlay);

        const form = document.getElementById('edit-pos-form');
        form.onsubmit = async (e) => {
            e.preventDefault();
            
            const parseNum = (val) => val === '' ? null : parseFloat(val);

            const updatedData = {
                latitude: parseFloat(document.getElementById('edit-lat').value),
                longitude: parseFloat(document.getElementById('edit-lon').value),
                altitude_ft: parseNum(document.getElementById('edit-alt').value),
                ground_speed_kts: parseNum(document.getElementById('edit-speed').value),
                heading: parseNum(document.getElementById('edit-heading').value),
                vertical_rate_fpm: parseNum(document.getElementById('edit-vrate').value)
            };

            try {
                await API.updatePosition(posId, updatedData);
                Utils.toast('Position report updated and flight stats recalculated.', 'success');
                overlay.remove();
                
                // Reload current flight to see recalculated stats and updated table
                this.selectFlight(this.selectedFlightId);
            } catch (err) {
                console.error("Failed to update position:", err);
                Utils.toast(`Error: ${err.message}`, 'error');
            }
        };
    },

    async deletePosition(posId) {
        if (!confirm("Are you sure you want to delete this position report? This will permanently remove this coordinate point from the flight path and immediately recalculate all speed, distance, and altitude statistics for this flight.")) return;

        try {
            await API.deletePosition(posId);
            Utils.toast('Position report deleted and flight stats recalculated.', 'success');
            
            // Reload current flight to see recalculated stats and updated table
            this.selectFlight(this.selectedFlightId);
        } catch (err) {
            console.error("Failed to delete position:", err);
            Utils.toast(`Error: ${err.message}`, 'error');
        }
    },

    async reassignPosition(posId) {
        const p = this.currentPositions.find(x => x.id === posId);
        if (!p) return;

        // Fetch flights for the same aircraft to suggest as targets
        let targetFlights = [];
        try {
            targetFlights = await API.getFlights({ limit: 100 });
            // Filter flights matching the aircraft tail or ID to prioritize them
            targetFlights = targetFlights.filter(f => f.aircraft_id === p.aircraft_id);
        } catch (err) {
            console.warn("Failed to fetch target flights for reassignment, using all flights:", err);
        }

        const overlay = document.createElement('div');
        overlay.className = 'popover-overlay';
        overlay.id = 'reassign-pos-popover';

        let optionsHtml = '';
        targetFlights.forEach(f => {
            const routeStr = `${f.departure_iata || '???'} → ${f.arrival_iata || '???'}`;
            const label = `${f.flight_number || f.callsign || 'Unknown'} (${routeStr}) - ${f.status} (${new Date(f.created_at).toLocaleDateString()})`;
            optionsHtml += `<option value="${f.id}" ${f.id === this.selectedFlightId ? 'selected' : ''}>${label}</option>`;
        });

        overlay.innerHTML = `
            <div class="modal" style="max-width: 440px; padding: 20px; animation: slideUp 0.2s ease;">
                <h3 style="margin-bottom: 12px; font-weight: 600;">Reassign Position Report #${p.id}</h3>
                <p style="font-size: 12px; color: var(--text-secondary); margin-bottom: 16px; line-height: 1.4;">
                    Move this telemetry point to a different flight. This is useful for cleaning up overlapping flights or incorrectly assigned telemetry chunks.
                </p>
                <form id="reassign-pos-form" style="display: flex; flex-direction: column; gap: 16px;">
                    <div class="form-group">
                        <label style="font-size: 11px;">Target Flight</label>
                        <select id="reassign-flight-select" class="input-field" style="height: auto; padding: 8px;">
                            ${optionsHtml || '<option value="">No other flights found</option>'}
                        </select>
                    </div>
                    <div style="display: flex; justify-content: flex-end; gap: 8px;">
                        <button type="button" class="btn-ghost" onclick="document.getElementById('reassign-pos-popover').remove()">Cancel</button>
                        <button type="submit" class="btn-primary" ${targetFlights.length === 0 ? 'disabled' : ''}>Reassign Point</button>
                    </div>
                </form>
            </div>
        `;

        document.body.appendChild(overlay);

        const form = document.getElementById('reassign-pos-form');
        form.onsubmit = async (e) => {
            e.preventDefault();
            const targetFlightId = document.getElementById('reassign-flight-select').value;
            if (!targetFlightId) return;

            try {
                await API.updatePosition(posId, { flight_id: targetFlightId });
                Utils.toast('Position report reassigned successfully. Stats updated for both flights.', 'success');
                overlay.remove();
                
                // Reload current flight workspace
                this.selectFlight(this.selectedFlightId);
            } catch (err) {
                console.error("Failed to reassign position:", err);
                Utils.toast(`Error: ${err.message}`, 'error');
            }
        };
    }
};

window.TelemetryAuditor = TelemetryAuditor;
