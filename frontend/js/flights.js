/**
 * Planey - Flights & Aircraft Panel Management
 * Handles sidebar rendering, modals, and CRUD actions
 */

const Flights = {
    aircraft: [],
    flights: [],
    selectedAircraftId: null,
    editingAircraftId: null,
    editingFlightId: null,
    currentFilter: 'active',
    aircraftFilter: 'all',
    aircraftSort: 'status',
    manuallyToggledCollapse: new Map(), // aircraftId -> boolean (isCollapsed)

    init() {
        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById(`panel-${btn.dataset.tab}`).classList.add('active');
                
                // Hide timeline when switching away from history tab
                if (btn.dataset.tab !== 'history' && window.Timeline && Timeline.aircraftId) {
                    Timeline.hide();
                }

                if (btn.dataset.tab === 'history') this._loadHistory();
            });
        });

        // Flight filter buttons
        document.querySelectorAll('.filter-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const parent = btn.parentElement;
                parent.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
                
                if (parent.id === 'aircraft-filters') {
                    this.aircraftFilter = btn.dataset.filter;
                    this._renderAircraftList();
                } else {
                    this.currentFilter = btn.dataset.filter;
                    this.loadFlights();
                }
            });
        });

        // Aircraft sort dropdown
        const sortSelect = document.getElementById('aircraft-sort');
        if (sortSelect) {
            sortSelect.addEventListener('change', (e) => {
                this.aircraftSort = e.target.value;
                this._renderAircraftList();
            });
        }

        // Add Aircraft modal
        document.getElementById('btn-add-aircraft').addEventListener('click', () => this._showAddAircraftModal());
        document.getElementById('btn-close-add-aircraft').addEventListener('click', () => this._hideModal('modal-add-aircraft'));
        document.getElementById('btn-cancel-add-aircraft').addEventListener('click', () => this._hideModal('modal-add-aircraft'));
        document.getElementById('btn-confirm-add-aircraft').addEventListener('click', () => this.editingAircraftId ? this._updateAircraft() : this._addAircraft());
        document.getElementById('btn-lookup').addEventListener('click', () => this._lookupAircraft());

        // Add Flight modal
        document.getElementById('btn-close-add-flight').addEventListener('click', () => this._hideModal('modal-add-flight'));
        document.getElementById('btn-cancel-add-flight').addEventListener('click', () => this._hideModal('modal-add-flight'));
        document.getElementById('btn-confirm-add-flight').addEventListener('click', () => this.editingFlightId ? this._updateFlight() : this._addFlight());

        // Sidebar toggle (mobile)
        document.getElementById('btn-toggle-sidebar').addEventListener('click', () => {
            document.getElementById('sidebar').classList.toggle('open');
        });

        // Close sidebar when clicking map on mobile
        document.getElementById('map').addEventListener('click', () => {
            if (window.innerWidth <= 768) {
                document.getElementById('sidebar').classList.remove('open');
            }
        });
    },

    // ── Data Loading ──

    async loadAircraft() {
        try {
            this.aircraft = await API.getAircraft();
            this._renderAircraftList();
            this._updateAircraftSelect();
            this._updateHistorySelect();
            this._updateStats();

            // Update map markers from latest positions
            for (const ac of this.aircraft) {
                if (ac.latest_position) {
                    FlightMap.updateMarker(ac.id, {
                        ...ac.latest_position,
                        tail_number: ac.tail_number,
                        flight_number: ac.active_flight?.flight_number,
                        departure_iata: ac.active_flight?.departure_iata,
                        arrival_iata: ac.active_flight?.arrival_iata,
                    });
                }
                if (ac.active_flight && ac.active_flight.status === 'scheduled') {
                    FlightMap.drawPlannedRoute(ac.id, ac.active_flight);
                } else {
                    if (FlightMap.plannedRoutes[ac.id]) {
                        FlightMap.plannedRoutes[ac.id].remove();
                        delete FlightMap.plannedRoutes[ac.id];
                    }
                }
            }
        } catch (err) {
            Utils.toast('Failed to load aircraft', 'error');
            console.error(err);
        }
    },

    /** Load recent history for all active aircraft to draw trails on startup */
    async loadInitialTrails() {
        const promises = this.aircraft.map(async (ac) => {
            try {
                // Fetch last 4 hours of positions for each aircraft
                const positions = await API.getPositionHistory(ac.id, 4);
                if (positions && positions.length > 0) {
                    FlightMap.drawTrail(ac.id, positions);
                    // Also ensure marker is at the latest position
                    const latest = positions[positions.length - 1];
                    FlightMap.updateMarker(ac.id, {
                        ...latest,
                        tail_number: ac.tail_number,
                        flight_number: ac.active_flight?.flight_number,
                        departure_iata: ac.active_flight?.departure_iata,
                        arrival_iata: ac.active_flight?.arrival_iata,
                    });
                }
            } catch (err) {
                console.warn(`Failed to load initial trail for ${ac.tail_number}:`, err);
            }
        });
        await Promise.all(promises);
    },

    async loadFlights() {
        try {
            const params = {};
            if (this.currentFilter === 'active') params.status = 'active,scheduled';
            else if (this.currentFilter !== 'all') params.status = this.currentFilter;

            this.flights = await API.getFlights(params);
            this._renderFlightsList();
        } catch (err) {
            Utils.toast('Failed to load flights', 'error');
            console.error(err);
        }
    },

    async _loadHistory() {
        const select = document.getElementById('history-aircraft-select');
        const hours = parseInt(document.getElementById('history-hours').value) || 24;
        const aircraftId = select.value;

        if (!aircraftId) {
            // Show completed flights
            try {
                const flights = await API.getFlights({ status: 'landed', limit: 20 });
                this._renderHistoryList(flights);
            } catch (err) { console.error(err); }
            return;
        }

        // Show position history for specific aircraft
        const ac = this.aircraft.find(a => a.id === aircraftId);
        if (ac) {
            Timeline.showHistory(aircraftId, ac.tail_number, hours);
        }
    },

    async _updateStats() {
        try {
            const stats = await API.getStats();
            document.getElementById('stat-aircraft-count').textContent = stats.active_aircraft || 0;
            document.getElementById('stat-active-flights').textContent = stats.active_flights || 0;
            document.getElementById('stat-positions').textContent = (stats.total_positions || 0).toLocaleString();
        } catch (err) { console.error(err); }
    },

    // ── Rendering ──

    _renderAircraftList() {
        const list = document.getElementById('aircraft-list');
        const empty = document.getElementById('aircraft-empty');

        if (this.aircraft.length === 0) {
            empty.style.display = '';
            // Remove all cards but keep empty state
            list.querySelectorAll('.aircraft-card').forEach(c => c.remove());
            return;
        }
        empty.style.display = 'none';

        // Remove old cards
        list.querySelectorAll('.aircraft-card').forEach(c => c.remove());

        let displayList = [...this.aircraft];

        // Apply Filters
        if (this.aircraftFilter === 'active') {
            displayList = displayList.filter(a => a.active_flight || (!a.latest_position?.on_ground && a.latest_position));
        } else if (this.aircraftFilter === 'ground') {
            displayList = displayList.filter(a => !a.active_flight && (a.latest_position?.on_ground || !a.latest_position));
        }

        // Apply Sorting
        if (this.aircraftSort === 'status') {
            // Active flights first, then recently updated
            displayList.sort((a, b) => {
                const aActive = a.active_flight || (!a.latest_position?.on_ground && a.latest_position) ? 1 : 0;
                const bActive = b.active_flight || (!b.latest_position?.on_ground && b.latest_position) ? 1 : 0;
                if (aActive !== bActive) return bActive - aActive;
                const aTime = a.latest_position?.timestamp ? new Date(a.latest_position.timestamp).getTime() : 0;
                const bTime = b.latest_position?.timestamp ? new Date(b.latest_position.timestamp).getTime() : 0;
                return bTime - aTime;
            });
        } else if (this.aircraftSort === 'tail') {
            displayList.sort((a, b) => (a.tail_number || '').localeCompare(b.tail_number || ''));
        } else if (this.aircraftSort === 'updated') {
            displayList.sort((a, b) => {
                const aTime = a.latest_position?.timestamp ? new Date(a.latest_position.timestamp).getTime() : 0;
                const bTime = b.latest_position?.timestamp ? new Date(b.latest_position.timestamp).getTime() : 0;
                return bTime - aTime;
            });
        }

        if (displayList.length === 0) {
            empty.style.display = '';
            return;
        }

        for (const ac of displayList) {
            const card = document.createElement('div');
            const pos = ac.latest_position;
            const flight = ac.active_flight;
            const statusText = flight ? flight.status : (pos?.on_ground ? 'ground' : 'unknown');
            const isActive = flight || (!pos?.on_ground && pos);

            let isCollapsed = !isActive; // Default to collapsed if inactive
            if (this.manuallyToggledCollapse.has(ac.id)) {
                isCollapsed = this.manuallyToggledCollapse.get(ac.id);
            }

            card.className = `aircraft-card${ac.id === this.selectedAircraftId ? ' selected' : ''}${isCollapsed ? ' collapsed' : ''}`;
            card.dataset.id = ac.id;

            card.innerHTML = `
                <div class="aircraft-card-header">
                    <div>
                        <div class="aircraft-tail">${ac.tail_number}</div>
                        <div class="aircraft-type">${ac.aircraft_type || 'Unknown type'}${ac.airline ? ` · ${ac.airline}` : ''}</div>
                        ${pos ? `<div class="aircraft-collapsed-location" id="loc-collapsed-${ac.id}">Loading last location...</div>` : ''}
                        ${pos ? `<div class="aircraft-collapsed-activity">${pos.on_ground ? 'Landed' : 'Active'}: <span class="${pos.on_ground ? '' : 'live-time-ago'}" data-timestamp="${pos.on_ground ? '' : pos.timestamp}">${pos.on_ground ? Utils.formatDateTime(pos.timestamp) : Utils.timeAgo(pos.timestamp)}</span></div>` : ''}
                    </div>
                    <div style="display: flex; gap: 8px; align-items: center;">
                        <button class="btn-primary btn-xs btn-add-flight-for" data-id="${ac.id}" style="padding: 2px 6px; font-size: 10px;">+ Flight</button>
                        ${Utils.statusBadge(statusText)}
                        <button class="btn-collapse" title="Toggle Details">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="6 9 12 15 18 9"></polyline></svg>
                        </button>
                    </div>
                </div>
                <div class="aircraft-card-body">
                    ${flight ? `
                        <div class="flight-route-container">
                            <div class="flight-route">
                                <div class="route-point">
                                    <span class="flight-airport">${flight.departure_iata || '???'}</span>
                                    ${flight.departure_name ? `<span class="airport-name">${flight.departure_name}</span>` : ''}
                                </div>
                                <span class="flight-arrow"><span class="flight-arrow-line"></span>✈<span class="flight-arrow-line"></span></span>
                                <div class="route-point">
                                    <span class="flight-airport">${flight.arrival_iata || '???'}</span>
                                    ${flight.arrival_name ? `<span class="airport-name">${flight.arrival_name}</span>` : ''}
                                </div>
                            </div>
                            <div class="flight-times">
                                ${flight.actual_departure ? `<span>🛫 ${Utils.formatDateTime(flight.actual_departure)}</span>` : 
                                  (flight.scheduled_departure ? `<span>🛫 ${Utils.formatDateTime(flight.scheduled_departure)}</span>` : '')}
                                ${flight.scheduled_arrival ? `<span>🛬 ${Utils.formatDateTime(flight.scheduled_arrival)}</span>` : ''}
                            </div>
                            ${flight.expected_route ? `
                            <div class="flight-expected-route" style="margin-top: 8px; font-size: 11px; color: var(--text-muted); background: var(--surface-bg); padding: 6px; border-radius: 4px; word-break: break-all;">
                                <strong style="color: var(--text-color);">Expected Route:</strong> ${flight.expected_route}
                            </div>` : ''}
                            ${flight.status === 'active' ? `
                            <div style="margin-top: 8px; display: flex; justify-content: flex-end;">
                                <button class="btn-secondary btn-xs btn-reconcile-flight" data-id="${flight.id}" title="Force close a stuck flight if it has landed">Reconcile Flight</button>
                            </div>` : ''}
                        </div>
                    ` : ''}
                    ${pos ? `
                        <div class="aircraft-details-grid">
                            <div class="aircraft-detail" style="grid-column: span 2;">
                                <span class="aircraft-detail-label">Location</span>
                                <span class="aircraft-detail-value" id="loc-${ac.id}">Loading...</span>
                            </div>
                            <div class="aircraft-detail">
                                <span class="aircraft-detail-label">Alt</span>
                                <span class="aircraft-detail-value">${pos.on_ground ? 'Ground' : Utils.formatAlt(pos.altitude_ft)}</span>
                            </div>
                            <div class="aircraft-detail">
                                <span class="aircraft-detail-label">Speed</span>
                                <span class="aircraft-detail-value">${pos.on_ground ? '0 kts' : Utils.formatSpeed(pos.ground_speed_kts)}</span>
                            </div>
                            <div class="aircraft-detail">
                                <span class="aircraft-detail-label">Heading</span>
                                <span class="aircraft-detail-value">${pos.on_ground ? '—' : Math.round(pos.heading || 0) + '°'}</span>
                            </div>
                            <div class="aircraft-detail">
                                <span class="aircraft-detail-label">${pos.on_ground ? 'Arrived' : 'Updated'}</span>
                                <span class="aircraft-detail-value ${pos.on_ground ? '' : 'live-time-ago'}" data-timestamp="${pos.on_ground ? '' : pos.timestamp}">${pos.on_ground ? Utils.formatDateTime(pos.timestamp) : Utils.timeAgo(pos.timestamp)}</span>
                            </div>
                        </div>
                    ` : '<div style="font-size:12px;color:var(--text-muted)">No position data</div>'}
                </div>
                
                <div class="aircraft-actions-modern">
                    <div class="primary-actions">
                        <button class="btn-secondary btn-poll-ac" data-id="${ac.id}" title="Poll current location">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-9.21l5.65-5.65"/></svg>
                            Live Pos
                        </button>
                        <button class="btn-secondary btn-view-history" data-id="${ac.id}" data-tail="${ac.tail_number}">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                            History
                        </button>
                    </div>
                    <div class="icon-toolbar">
                        <button class="btn-icon-small btn-sync-fa" data-id="${ac.id}" title="Sync schedules from FlightAware">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                        </button>
                        <button class="btn-icon-small btn-edit-aircraft" data-id="${ac.id}" title="Edit Aircraft">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                        </button>
                        <button class="btn-icon-small btn-delete-aircraft delete-icon" data-id="${ac.id}" data-tail="${ac.tail_number}" title="Remove Aircraft">
                            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                        </button>
                    </div>
                </div>
            `;

            // Click to focus on map
            card.addEventListener('click', (e) => {
                if (e.target.closest('button')) return;
                
                // If a timeline is active, hide it to clear the history trail and return to real-time
                if (window.Timeline && Timeline.aircraftId) {
                    Timeline.hide();
                }
                
                this.selectedAircraftId = ac.id;
                this._renderAircraftList();
                FlightMap.focusAircraft(ac.id);
            });

            // Click to toggle collapse
            const collapseBtn = card.querySelector('.btn-collapse');
            if (collapseBtn) {
                collapseBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    const isCurrentlyCollapsed = card.classList.contains('collapsed');
                    if (isCurrentlyCollapsed) {
                        card.classList.remove('collapsed');
                        this.manuallyToggledCollapse.set(ac.id, false);
                    } else {
                        card.classList.add('collapsed');
                        this.manuallyToggledCollapse.set(ac.id, true);
                    }
                });
            }

            list.appendChild(card);
            
            // Async load location name
            if (pos && pos.latitude && pos.longitude) {
                Utils.getLocationName(pos.latitude, pos.longitude).then(name => {
                    const el = document.getElementById(`loc-${ac.id}`);
                    if (el) el.textContent = name;
                    const elCollapsed = document.getElementById(`loc-collapsed-${ac.id}`);
                    if (elCollapsed) elCollapsed.textContent = name;
                });
            }
        }

        // Button handlers
        list.querySelectorAll('.btn-add-flight-for').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._showAddFlightModal(btn.dataset.id);
            });
        });
        
        list.querySelectorAll('.btn-edit-aircraft').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._showEditAircraftModal(btn.dataset.id);
            });
        });

        list.querySelectorAll('.btn-sync-fa').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._syncAircraftFA(btn.dataset.id, btn);
            });
        });

        list.querySelectorAll('.btn-poll-ac').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._pollAircraft(btn.dataset.id, btn);
            });
        });

        list.querySelectorAll('.btn-reconcile-flight').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._reconcileFlight(btn.dataset.id, btn);
            });
        });

        list.querySelectorAll('.btn-view-history').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                Timeline.showHistory(btn.dataset.id, btn.dataset.tail, 24);
            });
        });

        list.querySelectorAll('.btn-delete-aircraft').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._deleteAircraft(btn.dataset.id, btn.dataset.tail);
            });
        });
    },

    _renderFlightsList() {
        const list = document.getElementById('flights-list');
        const empty = document.getElementById('flights-empty');

        list.querySelectorAll('.flight-card').forEach(c => c.remove());

        if (this.flights.length === 0) {
            empty.style.display = '';
            return;
        }
        empty.style.display = 'none';

        for (const f of this.flights) {
            const card = document.createElement('div');
            card.className = 'flight-card';
            card.dataset.id = f.id;

            card.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div class="flight-number">${f.flight_number || f.callsign || 'Unknown'}</div>
                    ${Utils.statusBadge(f.status)}
                </div>
                <div class="flight-route-container" style="padding: 10px 0;">
                    <div class="flight-route">
                        <div class="route-point">
                            <span class="flight-airport">${f.departure_iata || '???'}</span>
                            ${f.departure_name ? `<span class="airport-name">${f.departure_name}</span>` : ''}
                        </div>
                        <span class="flight-arrow"><span class="flight-arrow-line"></span>✈<span class="flight-arrow-line"></span></span>
                        <div class="route-point">
                            <span class="flight-airport">${f.arrival_iata || '???'}</span>
                            ${f.arrival_name ? `<span class="airport-name">${f.arrival_name}</span>` : ''}
                        </div>
                    </div>
                    <div class="flight-times">
                        ${f.actual_departure ? `<span>🛫 ${Utils.formatDateTime(f.actual_departure)}</span>` : 
                          (f.scheduled_departure ? `<span>🛫 ${Utils.formatDateTime(f.scheduled_departure)}</span>` : '')}
                        ${f.scheduled_arrival ? `<span>🛬 ${Utils.formatDateTime(f.scheduled_arrival)}</span>` : ''}
                    </div>
                </div>
                <div class="aircraft-actions" style="margin-top:8px">
                    <button class="btn-primary btn-xs btn-view-flight" data-id="${f.id}" data-aircraft="${f.aircraft_id}">View Trail</button>
                    <button class="btn-secondary btn-xs btn-edit-flight" data-id="${f.id}">Edit</button>
                    <button class="btn-danger btn-xs btn-delete-flight" data-id="${f.id}">Delete</button>
                </div>
            `;

            list.appendChild(card);
        }

        list.querySelectorAll('.btn-view-flight').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const f = this.flights.find(fl => fl.id === btn.dataset.id);
                const title = f ? `${f.flight_number || ''} ${f.departure_iata || ''}→${f.arrival_iata || ''}` : 'Flight';
                Timeline.showFlight(btn.dataset.id, btn.dataset.aircraft, title);
            });
        });

        list.querySelectorAll('.btn-edit-flight').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                this._showEditFlightModal(btn.dataset.id);
            });
        });

        list.querySelectorAll('.btn-delete-flight').forEach(btn => {
            btn.addEventListener('click', async (e) => {
                e.stopPropagation();
                if (!confirm('Delete this flight and its positions?')) return;
                try {
                    await API.deleteFlight(btn.dataset.id);
                    Utils.toast('Flight deleted', 'success');
                    this.loadFlights();
                } catch (err) { Utils.toast(err.message, 'error'); }
            });
        });
    },

    _renderHistoryList(flights) {
        const list = document.getElementById('history-list');
        const empty = document.getElementById('history-empty');

        list.querySelectorAll('.flight-card').forEach(c => c.remove());

        if (!flights || flights.length === 0) {
            empty.style.display = '';
            return;
        }
        empty.style.display = 'none';

        for (const f of flights) {
            const card = document.createElement('div');
            card.className = 'flight-card';
            card.innerHTML = `
                <div style="display:flex;justify-content:space-between;align-items:flex-start">
                    <div class="flight-number">${f.flight_number || 'Unknown'}</div>
                    ${Utils.statusBadge(f.status)}
                </div>
                <div class="flight-route">
                    <span class="flight-airport" style="font-size:14px">${f.departure_iata || '???'}</span>
                    <span class="flight-arrow"><span class="flight-arrow-line" style="width:20px"></span>→<span class="flight-arrow-line" style="width:20px"></span></span>
                    <span class="flight-airport" style="font-size:14px">${f.arrival_iata || '???'}</span>
                </div>
                <div class="flight-meta">
                    <span>${Utils.formatDateTime(f.actual_departure || f.scheduled_departure)}</span>
                </div>
            `;
            card.addEventListener('click', () => {
                Timeline.showFlight(f.id, f.aircraft_id, `${f.flight_number || ''} ${f.departure_iata}→${f.arrival_iata}`);
            });
            list.appendChild(card);
        }
    },

    // ── Modals & CRUD ──

    _showModal(id) { document.getElementById(id).style.display = ''; },
    _hideModal(id) { document.getElementById(id).style.display = 'none'; },

    async _lookupAircraft() {
        const tail = document.getElementById('input-tail-number').value.trim();
        if (!tail) { Utils.toast('Enter a tail number first', 'warning'); return; }

        try {
            const data = await API.lookupAircraft({ tail_number: tail });
            if (data) {
                document.getElementById('lookup-result').style.display = '';
                document.getElementById('lookup-body').innerHTML = `
                    ${data.icao24_hex ? `<div class="lookup-row"><span class="lookup-label">ICAO24</span><span class="lookup-value">${data.icao24_hex}</span></div>` : ''}
                    ${data.aircraft_type ? `<div class="lookup-row"><span class="lookup-label">Type</span><span class="lookup-value">${data.aircraft_type}</span></div>` : ''}
                    ${data.airline ? `<div class="lookup-row"><span class="lookup-label">Airline</span><span class="lookup-value">${data.airline}</span></div>` : ''}
                    ${data.flight_number ? `<div class="lookup-row"><span class="lookup-label">Flight</span><span class="lookup-value">${data.flight_number}</span></div>` : ''}
                    ${data.departure_iata ? `<div class="lookup-row"><span class="lookup-label">Route</span><span class="lookup-value">${data.departure_iata} → ${data.arrival_iata || '?'}</span></div>` : ''}
                    ${data.status ? `<div class="lookup-row"><span class="lookup-label">Status</span><span class="lookup-value">${data.status}</span></div>` : ''}
                `;
                // Auto-fill fields
                if (data.icao24_hex) document.getElementById('input-icao24').value = data.icao24_hex;
                if (data.aircraft_type) document.getElementById('input-aircraft-type').value = data.aircraft_type;
                if (data.airline) document.getElementById('input-airline').value = data.airline;
            }
        } catch (err) {
            Utils.toast(err.message || 'Lookup failed', 'warning');
            document.getElementById('lookup-result').style.display = 'none';
        }
    },

    async _addAircraft() {
        const tail = document.getElementById('input-tail-number').value.trim().toUpperCase();
        if (!tail) { Utils.toast('Tail number is required', 'warning'); return; }

        const data = {
            tail_number: tail,
            icao24_hex: document.getElementById('input-icao24').value.trim() || null,
            aircraft_type: document.getElementById('input-aircraft-type').value.trim() || null,
            airline: document.getElementById('input-airline').value.trim() || null,
        };

        try {
            await API.addAircraft(data);
            Utils.toast(`Aircraft ${tail} added`, 'success');
            this._hideModal('modal-add-aircraft');
            this._clearAddAircraftForm();
            await this.loadAircraft();
        } catch (err) {
            Utils.toast(err.message, 'error');
        }
    },

    async _reconcileFlight(flightId, btn) {
        if (!confirm('Attempt to automatically close this active flight using FlightRadar24 data?')) return;

        const origText = btn.textContent;
        btn.textContent = '...';
        btn.disabled = true;

        try {
            const response = await fetch(`/api/flights/${flightId}/reconcile`, {
                method: 'POST'
            });
            
            const data = await response.json();
            
            if (!response.ok) throw new Error(data.detail || 'Reconciliation failed');
            
            Utils.toast(`Reconciliation: ${data.message}`, data.status === 'success' ? 'success' : 'info');
            await this.loadAircraft();
        } catch (error) {
            console.error('Reconcile error:', error);
            Utils.toast(error.message, 'error');
        } finally {
            btn.textContent = origText;
            btn.disabled = false;
        }
    },

    _clearAddAircraftForm() {
        ['input-tail-number', 'input-icao24', 'input-aircraft-type', 'input-airline'].forEach(id => {
            document.getElementById(id).value = '';
        });
        document.getElementById('lookup-result').style.display = 'none';
    },

    _showAddAircraftModal() {
        this.editingAircraftId = null;
        document.getElementById('modal-aircraft-title').textContent = 'Add Aircraft';
        document.getElementById('btn-confirm-add-aircraft').textContent = 'Add Aircraft';
        this._clearAddAircraftForm();
        this._showModal('modal-add-aircraft');
    },

    _showEditAircraftModal(id) {
        const ac = this.aircraft.find(a => a.id === id);
        if (!ac) return;

        this.editingAircraftId = id;
        document.getElementById('modal-aircraft-title').textContent = 'Edit Aircraft';
        document.getElementById('btn-confirm-add-aircraft').textContent = 'Save Changes';
        
        document.getElementById('input-tail-number').value = ac.tail_number || '';
        document.getElementById('input-icao24').value = ac.icao24_hex || '';
        document.getElementById('input-aircraft-type').value = ac.aircraft_type || '';
        document.getElementById('input-airline').value = ac.airline || '';
        
        this._showModal('modal-add-aircraft');
    },

    async _syncAircraftFA(id, btn) {
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Syncing...';
        }
        
        try {
            const res = await API.syncAircraftFA(id);
            Utils.toast(res.message, res.count > 0 ? 'success' : 'info');
            if (res.count > 0) {
                await Promise.all([this.loadAircraft(), this.loadFlights()]);
            }
        } catch (err) {
            Utils.toast(err.message, 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.textContent = 'Sync FA';
            }
        }
    },

    async _pollAircraft(id, btn) {
        if (btn) {
            btn.disabled = true;
            btn.textContent = 'Polling...';
        }
        
        try {
            const pos = await API.pollAircraft(id);
            if (pos) {
                Utils.toast('Position updated', 'success');
                // Ensure real-time state is properly restored if it was in history mode
                Timeline.hide(); 
                await this.loadAircraft();
            } else {
                Utils.toast('No position found for aircraft', 'warning');
            }
        } catch (err) {
            Utils.toast(err.message, 'error');
        } finally {
            if (btn) {
                btn.disabled = false;
                btn.innerHTML = `<svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-9.21l5.65-5.65"/></svg> Live Pos`;
            }
        }
    },

    async _updateAircraft() {
        const id = this.editingAircraftId;
        const data = {
            tail_number: document.getElementById('input-tail-number').value.trim().toUpperCase(),
            icao24_hex: document.getElementById('input-icao24').value.trim() || null,
            aircraft_type: document.getElementById('input-aircraft-type').value.trim() || null,
            airline: document.getElementById('input-airline').value.trim() || null,
        };

        try {
            await API.updateAircraft(id, data);
            Utils.toast('Aircraft updated', 'success');
            this._hideModal('modal-add-aircraft');
            await this.loadAircraft();
        } catch (err) { Utils.toast(err.message, 'error'); }
    },

    _showAddFlightModal(aircraftId) {
        this.editingFlightId = null;
        document.getElementById('modal-flight-title').textContent = 'Add Flight';
        document.getElementById('btn-confirm-add-flight').textContent = 'Add Flight';
        this._clearAddFlightForm();
        this._updateAircraftSelect();
        if (aircraftId) document.getElementById('flight-aircraft-select').value = aircraftId;
        this._showModal('modal-add-flight');
    },

    _showEditFlightModal(id) {
        const f = this.flights.find(fl => fl.id === id);
        if (!f) return;

        this.editingFlightId = id;
        document.getElementById('modal-flight-title').textContent = 'Edit Flight';
        document.getElementById('btn-confirm-add-flight').textContent = 'Save Changes';
        
        this._updateAircraftSelect();
        document.getElementById('flight-aircraft-select').value = f.aircraft_id;
        document.getElementById('input-flight-number').value = f.flight_number || '';
        document.getElementById('input-departure').value = f.departure_iata || '';
        document.getElementById('input-arrival').value = f.arrival_iata || '';
        document.getElementById('input-flight-status').value = f.status || 'scheduled';
        
        if (f.scheduled_departure) {
            document.getElementById('input-dep-time').value = f.scheduled_departure.slice(0, 16);
        }
        if (f.scheduled_arrival) {
            document.getElementById('input-arr-time').value = f.scheduled_arrival.slice(0, 16);
        }
        
        this._showModal('modal-add-flight');
    },

    async _updateFlight() {
        const id = this.editingFlightId;
        const data = {
            aircraft_id: document.getElementById('flight-aircraft-select').value,
            flight_number: document.getElementById('input-flight-number').value.trim() || null,
            departure_iata: document.getElementById('input-departure').value.trim().toUpperCase() || null,
            arrival_iata: document.getElementById('input-arrival').value.trim().toUpperCase() || null,
            scheduled_departure: document.getElementById('input-dep-time').value || null,
            scheduled_arrival: document.getElementById('input-arr-time').value || null,
            status: document.getElementById('input-flight-status').value,
        };

        try {
            await API.updateFlight(id, data);
            Utils.toast('Flight updated', 'success');
            this._hideModal('modal-add-flight');
            await Promise.all([this.loadAircraft(), this.loadFlights()]);
        } catch (err) { Utils.toast(err.message, 'error'); }
    },

    _updateAircraftSelect() {
        const sel = document.getElementById('flight-aircraft-select');
        sel.innerHTML = '<option value="">Select Aircraft</option>';
        for (const ac of this.aircraft) {
            sel.innerHTML += `<option value="${ac.id}">${ac.tail_number}${ac.aircraft_type ? ` (${ac.aircraft_type})` : ''}</option>`;
        }
    },

    _updateHistorySelect() {
        const sel = document.getElementById('history-aircraft-select');
        const cur = sel.value;
        sel.innerHTML = '<option value="">All Aircraft</option>';
        for (const ac of this.aircraft) {
            sel.innerHTML += `<option value="${ac.id}">${ac.tail_number}</option>`;
        }
        sel.value = cur;
    },

    async _addFlight() {
        const aircraftId = document.getElementById('flight-aircraft-select').value;
        if (!aircraftId) { Utils.toast('Select an aircraft', 'warning'); return; }

        const data = {
            aircraft_id: aircraftId,
            flight_number: document.getElementById('input-flight-number').value.trim() || null,
            departure_iata: document.getElementById('input-departure').value.trim().toUpperCase() || null,
            arrival_iata: document.getElementById('input-arrival').value.trim().toUpperCase() || null,
            scheduled_departure: document.getElementById('input-dep-time').value || null,
            scheduled_arrival: document.getElementById('input-arr-time').value || null,
        };

        try {
            await API.addFlight(data);
            Utils.toast('Flight added', 'success');
            this._hideModal('modal-add-flight');
            this._clearAddFlightForm();
            await Promise.all([this.loadAircraft(), this.loadFlights()]);
        } catch (err) {
            Utils.toast(err.message, 'error');
        }
    },

    _clearAddFlightForm() {
        ['input-flight-number', 'input-departure', 'input-arrival', 'input-dep-time', 'input-arr-time'].forEach(id => {
            document.getElementById(id).value = '';
        });
    },

    async _deleteAircraft(id, tail) {
        if (!confirm(`Remove ${tail}? This deletes all flights and position history.`)) return;
        try {
            await API.deleteAircraft(id);
            FlightMap.removeMarker(id);
            Utils.toast(`${tail} removed`, 'success');
            await this.loadAircraft();
        } catch (err) { Utils.toast(err.message, 'error'); }
    },

    // ── WebSocket Handler ──

    handleWSMessage(msg) {
        if (msg.type === 'position_update') {
            // Update marker on map
            FlightMap.updateMarker(msg.aircraft_id, {
                ...msg.data,
                tail_number: msg.tail_number,
            });

            // Update aircraft card if visible
            const card = document.querySelector(`.aircraft-card[data-id="${msg.aircraft_id}"]`);
            if (card) {
                // Subtle pulse animation
                card.style.borderColor = 'var(--accent)';
                setTimeout(() => { card.style.borderColor = ''; }, 1000);
            }
        } else if (msg.type === 'flight_status') {
            Utils.toast(`Flight status: ${msg.old_status} → ${msg.new_status}`, 'info');
            this.loadAircraft();
            this.loadFlights();
        }
    }
};
