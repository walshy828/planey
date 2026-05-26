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
    currentFilter: 'all',
    aircraftFilter: 'all',
    aircraftSort: 'status',
    _openDrawers: new Set(), // aircraftIds with open flight drawers

    init() {
        // Tab switching
        document.querySelectorAll('.tab-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
                btn.classList.add('active');
                document.getElementById(`panel-${btn.dataset.tab}`).classList.add('active');
                
                // Hide timeline when switching away from flights tab
                if (btn.dataset.tab !== 'flights' && window.Timeline && Timeline.aircraftId) {
                    Timeline.hide();
                }
            });
        });

        // History dropdown filters in consolidated flights tab
        const histAcSelect = document.getElementById('history-aircraft-select');
        const histLookbackSelect = document.getElementById('history-lookback-select');
        if (histAcSelect) {
            histAcSelect.addEventListener('change', () => this.loadFlights());
        }
        if (histLookbackSelect) {
            histLookbackSelect.addEventListener('change', () => this.loadFlights());
        }

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
                        category: ac.category,
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
                        category: ac.category,
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
            const subrow = document.getElementById('history-controls-subrow');
            
            if (this.currentFilter === 'completed' || this.currentFilter === 'all') {
                if (subrow) subrow.style.display = 'flex';
                
                const acSelect = document.getElementById('history-aircraft-select');
                const lookbackSelect = document.getElementById('history-lookback-select');
                
                if (acSelect && acSelect.value) {
                    params.aircraft_id = acSelect.value;
                }
                
                if (lookbackSelect && lookbackSelect.value !== 'all') {
                    params.hours = parseInt(lookbackSelect.value);
                }
                
                if (this.currentFilter === 'completed') {
                    params.status = 'landed';
                }
            } else {
                if (subrow) subrow.style.display = 'none';
                
                if (this.currentFilter === 'active') {
                    params.status = 'active';
                } else if (this.currentFilter === 'scheduled') {
                    params.status = 'scheduled';
                }
            }

            this.flights = await API.getFlights(params);
            this._renderFlightsList();
        } catch (err) {
            Utils.toast('Failed to load flights', 'error');
            console.error(err);
        }
    },

    async _updateStats() {
        try {
            const stats = await API.getStats();
            document.getElementById('stat-aircraft-count').textContent = stats.active_aircraft || 0;

            const airborneCount = stats.active_flights || 0;
            const airborneEl = document.getElementById('airborne-count');
            const indicatorEl = document.getElementById('airborne-indicator');
            if (airborneEl) airborneEl.textContent = airborneCount;
            if (indicatorEl) {
                indicatorEl.className = 'airborne-indicator' + (airborneCount > 0 ? ' active' : '');
            }

            const todayEl = document.getElementById('stat-flights-today');
            if (todayEl) todayEl.textContent = stats.flights_today || 0;

            if (stats.tracker) {
                this._updateTrackerUI(stats.tracker);
            }
        } catch (err) { console.error(err); }
    },

    _updateTrackerUI(data) {
        if (!data) return;
        
        const dot = document.getElementById('tracker-dot');
        const text = document.getElementById('tracker-mode-text');
        const timeVal = document.getElementById('stat-last-poll-time');
        
        if (dot && text) {
            let dotClass = 'status-dot';
            if (data.last_poll_status === 'error') {
                dotClass += ' error';
            } else if (data.last_poll_status === 'polling') {
                dotClass += ' polling';
            } else if (data.is_airborne_mode) {
                dotClass += ' airborne';
            } else {
                dotClass += ' passive';
            }
            dot.className = dotClass;
            
            const mode = data.is_airborne_mode ? 'Airborne' : 'Passive';
            const interval = data.current_interval || 300;
            text.textContent = `${mode} (${interval}s)`;
            
            let statusDesc = 'Tracker: IDLE';
            if (data.last_poll_status === 'success') statusDesc = 'Tracker: Last poll successful';
            else if (data.last_poll_status === 'polling') statusDesc = 'Tracker: Polling now...';
            else if (data.last_poll_status === 'no_aircraft') statusDesc = 'Tracker: No active aircraft tracked';
            else if (data.last_poll_status === 'no_data') statusDesc = 'Tracker: Last query returned empty (Rate Limited/Out of range)';
            else if (data.last_poll_status === 'error') statusDesc = 'Tracker: Last poll encountered an error';
            
            const trackerEl = document.getElementById('status-tracker');
            if (trackerEl) {
                trackerEl.title = `${statusDesc}\nInterval: ${interval}s`;
            }
        }
        
        if (timeVal) {
            if (data.last_poll_time) {
                timeVal.dataset.timestamp = data.last_poll_time;
                timeVal.textContent = Utils.timeAgo(data.last_poll_time);
            } else {
                timeVal.dataset.timestamp = '';
                timeVal.textContent = 'Never';
            }
        }
    },

    // ── Rendering ──

    _renderAircraftList() {
        const list = document.getElementById('aircraft-list');
        const empty = document.getElementById('aircraft-empty');

        if (this.aircraft.length === 0) {
            empty.style.display = '';
            list.querySelectorAll('.aircraft-card').forEach(c => c.remove());
            return;
        }
        empty.style.display = 'none';
        list.querySelectorAll('.aircraft-card').forEach(c => c.remove());

        let displayList = [...this.aircraft];

        if (this.aircraftFilter === 'active') {
            displayList = displayList.filter(a => {
                const f = a.active_flight;
                const p = a.latest_position;
                return (f && f.status === 'active') || (p && !p.on_ground);
            });
        } else if (this.aircraftFilter === 'ground') {
            displayList = displayList.filter(a => {
                const f = a.active_flight;
                const p = a.latest_position;
                return (!f || f.status === 'scheduled') && (!p || p.on_ground);
            });
        }

        if (this.aircraftSort === 'status') {
            displayList.sort((a, b) => {
                const pri = ac => {
                    if (ac.active_flight?.status === 'active') return 3;
                    if (ac.latest_position && !ac.latest_position.on_ground) return 3;
                    if (ac.active_flight?.status === 'scheduled') return 2;
                    return 1;
                };
                const pa = pri(a), pb = pri(b);
                if (pa !== pb) return pb - pa;
                const ta = a.latest_position?.timestamp ? new Date(a.latest_position.timestamp).getTime() : 0;
                const tb = b.latest_position?.timestamp ? new Date(b.latest_position.timestamp).getTime() : 0;
                return tb - ta;
            });
        } else if (this.aircraftSort === 'tail') {
            displayList.sort((a, b) => (a.tail_number || '').localeCompare(b.tail_number || ''));
        } else if (this.aircraftSort === 'updated') {
            displayList.sort((a, b) => {
                const ta = a.latest_position?.timestamp ? new Date(a.latest_position.timestamp).getTime() : 0;
                const tb = b.latest_position?.timestamp ? new Date(b.latest_position.timestamp).getTime() : 0;
                return tb - ta;
            });
        }

        if (displayList.length === 0) { empty.style.display = ''; return; }

        for (const ac of displayList) {
            const card = document.createElement('div');
            const pos = ac.latest_position;
            const flight = ac.active_flight;
            const isHeli = ac.category === 'helicopter';
            const vehicleIcon = isHeli ? '🚁' : '✈';

            let statusText = 'unknown';
            let isAirborne = false;
            let isScheduled = false;
            if (flight) {
                statusText = flight.status;
                isAirborne = flight.status === 'active' && pos && !pos.on_ground;
                isScheduled = flight.status === 'scheduled';
            } else if (pos) {
                statusText = pos.on_ground ? 'ground' : 'active';
                isAirborne = !pos.on_ground;
            }

            // Subtitle: display_name OR aircraft_type + airline
            const subParts = [];
            if (ac.display_name) subParts.push(ac.display_name);
            else if (ac.aircraft_type) subParts.push(ac.aircraft_type);
            if (ac.airline) subParts.push(ac.airline);
            const acSub = subParts.join(' · ') || 'Unknown type';

            // Context section
            let contextHtml = '';
            if (flight && (flight.status === 'active' || flight.status === 'scheduled')) {
                const dep = flight.departure_iata || flight.departure_icao || '???';
                const arr = flight.arrival_iata || flight.arrival_icao || '???';
                const depName = flight.departure_name || '';
                const arrName = flight.arrival_name || '';

                let metaLine = '';
                if (isAirborne) {
                    const depTime = flight.actual_departure || flight.scheduled_departure;
                    const airborne = depTime ? Utils.formatAirborneTime(depTime) : null;
                    const parts = [];
                    if (depTime) parts.push(`Departed ${Utils.formatRelativeDate(depTime)} · ${Utils.formatTime(depTime)}`);
                    if (airborne) parts.push(`${airborne} airborne`);
                    if (flight.scheduled_arrival) parts.push(`ETA ${Utils.formatTime(flight.scheduled_arrival)}`);
                    metaLine = parts.join(' · ');
                } else if (isScheduled && flight.scheduled_departure) {
                    const rel = Utils.formatRelativeDate(flight.scheduled_departure);
                    const time = Utils.formatTime(flight.scheduled_departure);
                    metaLine = `Departs ${rel} · ${time}`;
                    if (flight.scheduled_arrival) metaLine += ` → ${Utils.formatTime(flight.scheduled_arrival)}`;
                }

                const flightNumTag = flight.flight_number
                    ? `<span class="ac-flight-id">${flight.flight_number}</span>`
                    : '';

                contextHtml = `<div class="ac-context${isAirborne ? ' ac-context-airborne' : (isScheduled ? ' ac-context-scheduled' : '')}">
                    ${flightNumTag}
                    <div class="ac-route-display">
                        <div class="ac-route-point">
                            <span class="ac-route-code">${dep}</span>
                            ${depName ? `<span class="ac-route-name">${depName}</span>` : ''}
                        </div>
                        <div class="ac-route-center">
                            <span class="ac-arrow-line"></span>
                            <span class="ac-arrow-icon">${vehicleIcon}</span>
                            <span class="ac-arrow-line"></span>
                        </div>
                        <div class="ac-route-point ac-route-right">
                            <span class="ac-route-code">${arr}</span>
                            ${arrName ? `<span class="ac-route-name" style="text-align:right">${arrName}</span>` : ''}
                        </div>
                    </div>
                    ${metaLine ? `<div class="ac-route-meta">${metaLine}</div>` : ''}
                    ${flight.expected_route && isScheduled ? `<div class="ac-expected-route" title="Expected IFR route">${flight.expected_route}</div>` : ''}
                </div>`;
            } else if (pos) {
                const timeText = pos.on_ground
                    ? `Landed ${Utils.formatRelativeDate(pos.timestamp)} · ${Utils.formatTime(pos.timestamp)}`
                    : `Active · <span class="live-time-ago" data-timestamp="${pos.timestamp}">${Utils.timeAgo(pos.timestamp)}</span>`;
                contextHtml = `<div class="ac-context ac-context-ground">
                    <span class="ac-loc-pin">📍</span>
                    <span class="ac-loc-name" id="loc-${ac.id}">Loading…</span>
                    <span class="ac-loc-time">${timeText}</span>
                </div>`;
            }

            // Live telemetry — only shown when actively airborne
            let telemHtml = '';
            if (isAirborne && pos) {
                const altStr = pos.altitude_ft != null
                    ? (pos.altitude_ft >= 18000
                        ? `FL${Math.round(pos.altitude_ft / 100)}`
                        : `${Math.round(pos.altitude_ft).toLocaleString()} ft`)
                    : '—';
                const vsStr = Utils.formatVRate(pos.vertical_rate_fpm);
                telemHtml = `<div class="ac-telemetry">
                    <div class="ac-telem-grid">
                        <div class="ac-telem-item">
                            <span class="ac-telem-label">Alt</span>
                            <span class="ac-telem-val">${altStr}</span>
                        </div>
                        <div class="ac-telem-item">
                            <span class="ac-telem-label">Speed</span>
                            <span class="ac-telem-val">${Utils.formatSpeed(pos.ground_speed_kts)}</span>
                        </div>
                        <div class="ac-telem-item">
                            <span class="ac-telem-label">Heading</span>
                            <span class="ac-telem-val">${pos.heading != null ? Math.round(pos.heading) + '°' : '—'}</span>
                        </div>
                        <div class="ac-telem-item">
                            <span class="ac-telem-label">V/S</span>
                            <span class="ac-telem-val">${vsStr}</span>
                        </div>
                    </div>
                    <div class="ac-telem-footer">
                        <span>Updated <span class="live-time-ago" data-timestamp="${pos.timestamp}">${Utils.timeAgo(pos.timestamp)}</span></span>
                        ${pos.squawk ? `<span class="ac-squawk">Squawk ${pos.squawk}</span>` : ''}
                    </div>
                </div>`;
            }

            // Reconcile hint for stuck active flights
            const reconcileHtml = (flight && flight.status === 'active')
                ? `<button class="btn-reconcile-hint btn-reconcile-flight" data-id="${flight.id}">Flight stuck on the ground? Reconcile →</button>`
                : '';

            const cardClasses = ['aircraft-card',
                ac.id === this.selectedAircraftId ? 'selected' : '',
                isAirborne ? 'is-airborne' : '',
                isScheduled ? 'is-scheduled' : '',
            ].filter(Boolean).join(' ');

            card.className = cardClasses;
            card.dataset.id = ac.id;

            card.innerHTML = `
                <div class="ac-header">
                    <div class="ac-identity">
                        <div class="ac-tail">${ac.tail_number}${isHeli ? ' <span class="ac-category-tag">HELI</span>' : ''}</div>
                        <div class="ac-sub">${acSub}</div>
                    </div>
                    <div class="ac-header-right">
                        ${Utils.statusBadge(statusText)}
                        <button class="btn-icon-small btn-aircraft-menu" data-id="${ac.id}" title="Options">
                            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="1.5"></circle><circle cx="12" cy="5" r="1.5"></circle><circle cx="12" cy="19" r="1.5"></circle></svg>
                        </button>
                    </div>
                </div>
                ${contextHtml}
                ${telemHtml}
                <div class="ac-actions">
                    <button class="btn-ac-action btn-poll-ac" data-id="${ac.id}" title="Fetch latest position from OpenSky">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round"><path d="M21.5 2v6h-6M21.34 15.57a10 10 0 1 1-.59-9.21l5.65-5.65"/></svg>
                        Refresh
                    </button>
                    <button class="btn-ac-action btn-view-history" data-id="${ac.id}" data-tail="${ac.tail_number}" title="View position history timeline">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="12" r="10"></circle><polyline points="12 6 12 12 16 14"></polyline></svg>
                        Timeline
                    </button>
                    <button class="btn-ac-action btn-toggle-flights${this._openDrawers.has(ac.id) ? ' btn-ac-action-open' : ''}" data-id="${ac.id}" title="View flights for this aircraft">
                        <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><path d="M17.8 19.2 16 11l3.5-3.5C21 6 21.5 4 21 3c-1-.5-3 0-4.5 1.5L13 8 4.8 6.2c-.5-.1-.9.1-1.1.5l-.3.5c-.2.5-.1 1 .3 1.3L9 12l-2 3H4l-1 1 3 2 2 3 1-1v-3l3-2 3.5 5.3c.3.4.8.5 1.3.3l.5-.2c.4-.3.6-.7.5-1.2z"/></svg>
                        Flights
                        <svg class="btn-flights-chevron${this._openDrawers.has(ac.id) ? ' open' : ''}" width="10" height="10" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="6 9 12 15 18 9"></polyline></svg>
                    </button>
                </div>
                ${reconcileHtml}
                <div class="ac-flights-drawer" id="drawer-${ac.id}" style="display:${this._openDrawers.has(ac.id) ? 'block' : 'none'}">
                    <div class="drawer-loading">Loading flights…</div>
                </div>`;

            card.addEventListener('click', (e) => {
                if (e.target.closest('button') || e.target.closest('.ac-flights-drawer')) return;
                if (window.Timeline && Timeline.aircraftId) Timeline.hide();
                this.selectedAircraftId = ac.id;
                this._renderAircraftList();
                FlightMap.focusAircraft(ac.id);
            });

            list.appendChild(card);

            // Restore open drawers after re-render
            if (this._openDrawers.has(ac.id)) {
                this._loadAircraftFlights(ac.id).then(flights => {
                    const drawerEl = document.getElementById(`drawer-${ac.id}`);
                    if (drawerEl) this._renderFlightsDrawerContent(ac.id, flights, drawerEl, ac.tail_number);
                });
            }

            // Geocode location for grounded aircraft
            if (pos && pos.latitude && pos.longitude && !flight) {
                Utils.getLocationName(pos.latitude, pos.longitude).then(name => {
                    const el = document.getElementById(`loc-${ac.id}`);
                    if (el) el.textContent = name;
                });
            }
        }

        list.querySelectorAll('.btn-aircraft-menu').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); this._showAircraftMenu(btn.dataset.id, btn); });
        });
        list.querySelectorAll('.btn-poll-ac').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); this._pollAircraft(btn.dataset.id, btn); });
        });
        list.querySelectorAll('.btn-view-history').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); Timeline.showHistory(btn.dataset.id, btn.dataset.tail, 24); });
        });
        list.querySelectorAll('.btn-toggle-flights').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); this._toggleFlightsDrawer(btn.dataset.id, btn); });
        });
        list.querySelectorAll('.btn-reconcile-flight').forEach(btn => {
            btn.addEventListener('click', (e) => { e.stopPropagation(); this._reconcileFlight(btn.dataset.id, btn); });
        });
    },

    // ── Flights Drawer (inline per-aircraft flight history) ──

    async _loadAircraftFlights(aircraftId) {
        try {
            return await API.getFlights({ aircraft_id: aircraftId, limit: 10 });
        } catch (err) {
            console.error('Failed to load flights for aircraft:', err);
            return [];
        }
    },

    async _toggleFlightsDrawer(aircraftId, btn) {
        const drawer = document.getElementById(`drawer-${aircraftId}`);
        if (!drawer) return;

        if (this._openDrawers.has(aircraftId)) {
            this._openDrawers.delete(aircraftId);
            drawer.style.display = 'none';
            btn.classList.remove('btn-ac-action-open');
            btn.querySelector('.btn-flights-chevron')?.classList.remove('open');
            return;
        }

        this._openDrawers.add(aircraftId);
        drawer.style.display = 'block';
        btn.classList.add('btn-ac-action-open');
        btn.querySelector('.btn-flights-chevron')?.classList.add('open');

        const ac = this.aircraft.find(a => a.id === aircraftId);
        const flights = await this._loadAircraftFlights(aircraftId);
        this._renderFlightsDrawerContent(aircraftId, flights, drawer, ac?.tail_number);
    },

    _renderFlightsDrawerContent(aircraftId, flights, drawerEl, tailNumber) {
        if (!flights || flights.length === 0) {
            drawerEl.innerHTML = '<div class="drawer-empty">No flight history found</div>';
            return;
        }

        const rows = flights.slice(0, 8).map(f => {
            const dt = f.actual_departure || f.scheduled_departure;
            const dateStr = dt ? Utils.formatRelativeDate(dt) : '—';
            const routeName = Utils.flightName(f, tailNumber);
            let duration = '—';
            if (f.summary_stats?.duration_seconds > 0) {
                duration = Utils.formatDuration(f.summary_stats.duration_seconds);
            } else if (f.actual_departure && f.actual_arrival) {
                const s = (new Date(f.actual_arrival) - new Date(f.actual_departure)) / 1000;
                if (s > 0) duration = Utils.formatDuration(s);
            }
            const canView = f.status === 'active' || f.status === 'landed';
            const fuelStopHtml = f.raw_data?.stop_type === 'fuel_stop' ? '<span class="dfr-fuel-stop">⛽</span>' : '';
            return `<div class="drawer-flight-row${canView ? ' clickable' : ''}" data-flight-id="${f.id}" data-aircraft-id="${aircraftId}" data-status="${f.status}">
                <span class="dfr-date">${dateStr}</span>
                <span class="dfr-route">${routeName}${fuelStopHtml}</span>
                <span class="dfr-duration">${duration}</span>
                <span class="dfr-badge">${Utils.statusBadge(f.status)}</span>
            </div>`;
        }).join('');

        drawerEl.innerHTML = `
            <div class="drawer-header">
                <span>Recent Flights</span>
                <span class="drawer-count">${flights.length} found</span>
            </div>
            ${rows}
            <a class="drawer-view-all" data-aircraft-id="${aircraftId}">View all in Flights tab →</a>`;

        drawerEl.querySelectorAll('.drawer-flight-row.clickable').forEach(row => {
            row.addEventListener('click', (e) => {
                e.stopPropagation();
                const fId = row.dataset.flightId;
                const aId = row.dataset.aircraftId;
                const f = flights.find(fl => fl.id === fId);
                if (f) Timeline.showFlight(fId, aId, Utils.flightName(f, tailNumber));
            });
        });

        drawerEl.querySelector('.drawer-view-all')?.addEventListener('click', (e) => {
            e.stopPropagation();
            this.goToFlightsForAircraft(aircraftId);
        });
    },

    goToFlightsForAircraft(aircraftId) {
        document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
        document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
        const tab = document.querySelector('[data-tab="flights"]');
        const panel = document.getElementById('panel-flights');
        if (tab) tab.classList.add('active');
        if (panel) panel.classList.add('active');

        const acSelect = document.getElementById('history-aircraft-select');
        if (acSelect) acSelect.value = aircraftId;

        const allBtn = document.getElementById('filter-all');
        if (allBtn) allBtn.click();
        else this.loadFlights();
    },

    _loadHistory() { this.loadFlights(); },

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
            card.dataset.id = f.id;

            const ac = this.aircraft.find(a => a.id === f.aircraft_id);
            const isHeli = ac?.category === 'helicopter';
            const vehicleIcon = isHeli ? '🚁' : '✈';
            const tailNumber = ac?.tail_number || '?';
            const acType = ac?.aircraft_type || '';

            // Show official flight ID only if it adds information beyond the route
            const fName = Utils.flightName(f, tailNumber);
            const hasOfficialId = f.flight_number || (f.callsign && f.callsign.toUpperCase() !== tailNumber.toUpperCase());
            const flightIdHtml = hasOfficialId ? `<span class="fl-flight-id">${fName}</span>` : '';

            // Route display — prefer IATA, fall back to short name
            const dep = f.departure_iata || f.departure_icao || '';
            const arr = f.arrival_iata || f.arrival_icao || '';
            const depName = f.departure_name || '';
            const arrName = f.arrival_name || '';
            const depDisplay = dep || (depName ? depName.split(',')[0].substring(0, 12) : '???');
            const arrDisplay = arr || (arrName ? arrName.split(',')[0].substring(0, 12) : '???');

            // Duration badge for route arrow area
            let durationBadge = '';
            if (f.summary_stats?.duration_seconds > 0) {
                durationBadge = `<span class="fl-duration-badge">${Utils.formatDuration(f.summary_stats.duration_seconds)}</span>`;
            } else if (f.actual_departure && f.actual_arrival) {
                const s = (new Date(f.actual_arrival) - new Date(f.actual_departure)) / 1000;
                if (s > 60) durationBadge = `<span class="fl-duration-badge">${Utils.formatDuration(s)}</span>`;
            }

            // Time row
            const depTime = f.actual_departure || f.scheduled_departure;
            const arrTime = f.actual_arrival || f.scheduled_arrival;
            let timeHtml = '';
            if (depTime) {
                const relDate = Utils.formatRelativeDate(depTime);
                const time = Utils.formatTime(depTime);
                const icon = f.actual_departure ? '🛫' : '🗓';
                const suffix = !f.actual_departure ? ' <span class="fl-time-sched">(sched)</span>' : '';
                timeHtml += `<span class="fl-time-item">${icon} ${relDate} · ${time}${suffix}</span>`;
            }
            if (arrTime) {
                const time = Utils.formatTime(arrTime);
                const icon = f.actual_arrival ? '🛬' : '→';
                const suffix = !f.actual_arrival ? ' <span class="fl-time-sched">(est)</span>' : '';
                timeHtml += `<span class="fl-time-item">${icon} ${time}${suffix}</span>`;
            }
            if (f.status === 'active' && depTime) {
                const airborne = Utils.formatAirborneTime(depTime);
                if (airborne) timeHtml += `<span class="fl-time-airborne">● ${airborne} airborne</span>`;
            }

            // Stats row (compact, single line)
            let statsHtml = '';
            if (f.summary_stats) {
                const s = f.summary_stats;
                const parts = [];
                if (s.duration_seconds > 0) parts.push(Utils.formatDuration(s.duration_seconds));
                if (s.distance_nm != null) parts.push(`${s.distance_nm.toFixed(0)} NM`);
                if (s.distance_sm != null) parts.push(`${s.distance_sm.toFixed(0)} mi`);
                if (s.max_altitude_ft != null && s.max_altitude_ft > 0) {
                    parts.push(s.max_altitude_ft >= 18000
                        ? `FL${Math.round(s.max_altitude_ft / 100)} peak`
                        : `${Math.round(s.max_altitude_ft).toLocaleString()} ft peak`);
                }
                if (s.avg_ground_speed_kts != null) parts.push(`${Math.round(s.avg_ground_speed_kts)} kts avg`);
                if (parts.length > 0) {
                    statsHtml = `<div class="fl-stats-row">${parts.join('<span class="fl-sep">·</span>')}</div>`;
                }
            }

            // Active flight live telemetry
            let liveHtml = '';
            if (f.status === 'active') {
                const activeFlight = this.aircraft.find(a => a.id === f.aircraft_id)?.active_flight;
                const pos = this.aircraft.find(a => a.id === f.aircraft_id)?.latest_position;
                if (pos && !pos.on_ground) {
                    const altStr = pos.altitude_ft != null
                        ? (pos.altitude_ft >= 18000 ? `FL${Math.round(pos.altitude_ft / 100)}` : `${Math.round(pos.altitude_ft).toLocaleString()} ft`)
                        : null;
                    const liveParts = [];
                    if (altStr) liveParts.push(altStr);
                    if (pos.ground_speed_kts != null) liveParts.push(`${Math.round(pos.ground_speed_kts)} kts`);
                    if (pos.heading != null) liveParts.push(`${Math.round(pos.heading)}° hdg`);
                    if (liveParts.length > 0) {
                        liveHtml = `<div class="fl-live-row">${liveParts.join('<span class="fl-sep">·</span>')}</div>`;
                    }
                }
            }

            // Expected route for scheduled flights
            const routeHtml = (f.expected_route && f.status === 'scheduled')
                ? `<div class="fl-expected-route">Route: ${f.expected_route}</div>`
                : '';

            // Fuel/technical stop badge from raw_data
            const stopType = f.raw_data?.stop_type;
            const stopBadgeHtml = stopType === 'fuel_stop'
                ? `<span class="fl-stop-badge">⛽ fuel stop</span>`
                : '';

            const statusClass = `status-${f.status}`;
            card.className = `flight-card ${statusClass}`;

            const canViewTrail = f.status === 'active' || f.status === 'landed';
            if (canViewTrail) card.style.cursor = 'pointer';

            card.innerHTML = `
                <div class="fl-header">
                    <div class="fl-aircraft">
                        <span class="fl-tail">${tailNumber}</span>
                        ${acType ? `<span class="fl-type">${acType}</span>` : ''}
                        ${flightIdHtml}
                    </div>
                    ${Utils.statusBadge(f.status)}
                </div>
                <div class="fl-route-block">
                    <div class="fl-route-end">
                        <div class="fl-iata${dep ? '' : ' fl-iata-sm'}">${depDisplay}</div>
                        ${depName && dep ? `<div class="fl-airport-name">${depName}</div>` : ''}
                    </div>
                    <div class="fl-route-center">
                        <div class="fl-route-arrow-wrap">
                            <span class="fl-arrow-line"></span>
                            <span class="fl-arrow-icon">${vehicleIcon}</span>
                            <span class="fl-arrow-line"></span>
                        </div>
                        ${durationBadge}
                    </div>
                    <div class="fl-route-end fl-route-end-right">
                        <div class="fl-iata${arr ? '' : ' fl-iata-sm'}">${arrDisplay}</div>
                        ${arrName && arr ? `<div class="fl-airport-name fl-airport-name-right">${arrName}</div>` : ''}
                    </div>
                </div>
                ${timeHtml ? `<div class="fl-times">${timeHtml}</div>` : ''}
                ${liveHtml}
                ${statsHtml}
                ${routeHtml}
                ${stopBadgeHtml}
                <div class="fl-actions">
                    ${canViewTrail ? `<button class="btn-xs btn-view-flight" data-id="${f.id}" data-aircraft="${f.aircraft_id}">View Trail</button>` : ''}
                    <button class="btn-xs btn-edit-flight" data-id="${f.id}">Edit</button>
                    <button class="btn-xs btn-delete-flight" data-id="${f.id}">Delete</button>
                </div>`;

            if (canViewTrail) {
                card.addEventListener('click', (e) => {
                    if (e.target.closest('.fl-actions button')) return;
                    Timeline.showFlight(f.id, f.aircraft_id, Utils.flightName(f, tailNumber));
                });
            }

            list.appendChild(card);
        }

        list.querySelectorAll('.btn-view-flight').forEach(btn => {
            btn.addEventListener('click', (e) => {
                e.stopPropagation();
                const f = this.flights.find(fl => fl.id === btn.dataset.id);
                const ac = this.aircraft.find(a => a.id === btn.dataset.aircraft);
                Timeline.showFlight(btn.dataset.id, btn.dataset.aircraft, f ? Utils.flightName(f, ac?.tail_number) : 'Flight');
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
                if (!confirm('Delete this flight and all its position data?')) return;
                try {
                    await API.deleteFlight(btn.dataset.id);
                    Utils.toast('Flight deleted', 'success');
                    this.loadFlights();
                } catch (err) { Utils.toast(err.message, 'error'); }
            });
        });
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
            category: document.getElementById('input-aircraft-category').value,
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
        document.getElementById('input-aircraft-category').value = 'plane';
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
        document.getElementById('input-aircraft-category').value = ac.category || 'plane';
        
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
            category: document.getElementById('input-aircraft-category').value,
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
        if (window.TelemetryAuditor) {
            window.TelemetryAuditor.open(id);
            return;
        }

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

    _showAircraftMenu(id, triggerBtn) {
        // Remove any existing dropdowns first
        const existing = document.getElementById('aircraft-context-menu');
        if (existing) {
            existing.remove();
        }

        const ac = this.aircraft.find(a => a.id === id);
        if (!ac) return;

        const pos = ac.latest_position;
        const flight = ac.active_flight;

        // Create the dropdown container
        const menu = document.createElement('div');
        menu.id = 'aircraft-context-menu';
        menu.className = 'aircraft-dropdown-menu';

        // Add options HTML
        let html = `
            <div class="dropdown-item btn-add-flight-for" data-id="${ac.id}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="5" x2="12" y2="19"></line><line x1="5" y1="12" x2="19" y2="12"></line></svg>
                Add Flight
            </div>
            <div class="dropdown-item btn-sync-fa" data-id="${ac.id}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v20M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"/></svg>
                Sync FlightAware schedules
            </div>
            <div class="dropdown-item btn-edit-aircraft" data-id="${ac.id}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg>
                Edit Aircraft Details
            </div>
            <div class="dropdown-item btn-delete-aircraft delete-icon" data-id="${ac.id}" data-tail="${ac.tail_number}">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="3 6 5 6 21 6"></polyline><path d="M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg>
                Remove Aircraft
            </div>
            <div class="dropdown-divider"></div>
            <div class="dropdown-header">External Tracking</div>
            <a class="dropdown-item external-link" href="https://www.flightaware.com/live/flight/${ac.tail_number}" target="_blank" rel="noopener noreferrer">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path><polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline><line x1="12" y1="22.08" x2="12" y2="12"></line></svg>
                FlightAware Live
            </a>
        `;

        if (ac.icao24_hex) {
            html += `
            <a class="dropdown-item external-link" href="https://globe.adsbexchange.com/?icao=${ac.icao24_hex.toUpperCase()}" target="_blank" rel="noopener noreferrer">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><circle cx="12" cy="12" r="1"></circle><circle cx="12" cy="12" r="10"></circle><path d="M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10 15.3 15.3 0 0 1-4-10 15.3 15.3 0 0 1 4-10z"></path><path d="M2 12h20"></path></svg>
                ADS-B Exchange
            </a>
            `;
        }

        // Determine how to pass location to OpenSky
        let openskyUrl = 'https://map.opensky-network.org/';
        if (pos && pos.latitude && pos.longitude) {
            openskyUrl += `?lat=${pos.latitude}&lon=${pos.longitude}&zoom=10#lat=${pos.latitude}&lon=${pos.longitude}&zoom=10`;
        }

        html += `
            <a class="dropdown-item external-link" href="${openskyUrl}" target="_blank" rel="noopener noreferrer">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M18 10h-1.26A8 8 0 1 0 9 20h9a5 5 0 0 0 0-10z"></path></svg>
                OpenSky Network Map
            </a>
        `;

        if (flight && (flight.arrival_icao || flight.arrival_iata)) {
            const code = (flight.arrival_icao || flight.arrival_iata).toUpperCase();
            html += `
            <div class="dropdown-divider"></div>
            <div class="dropdown-header">Radio / ATC</div>
            <a class="dropdown-item external-link" href="https://www.liveatc.net/search/?icao=${code}" target="_blank" rel="noopener noreferrer">
                <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M12 1a3 3 0 0 0-3 3v8a3 3 0 0 0 6 0V4a3 3 0 0 0-3-3z"></path><path d="M19 10v2a7 7 0 0 1-14 0v-2"></path><line x1="12" y1="19" x2="12" y2="23"></line><line x1="8" y1="23" x2="16" y2="23"></line></svg>
                LiveATC - ${code} Tower
            </a>
            `;
        }

        menu.innerHTML = html;
        document.body.appendChild(menu);

        // Bind inner actions
        menu.querySelector('.btn-add-flight-for').addEventListener('click', (e) => {
            e.stopPropagation();
            menu.remove();
            this._showAddFlightModal(id);
        });

        menu.querySelector('.btn-sync-fa').addEventListener('click', (e) => {
            e.stopPropagation();
            menu.remove();
            this._syncAircraftFA(id);
        });

        menu.querySelector('.btn-edit-aircraft').addEventListener('click', (e) => {
            e.stopPropagation();
            menu.remove();
            this._showEditAircraftModal(id);
        });

        menu.querySelector('.btn-delete-aircraft').addEventListener('click', (e) => {
            e.stopPropagation();
            menu.remove();
            this._deleteAircraft(id, ac.tail_number);
        });

        // Close menu on click of any external link
        menu.querySelectorAll('.external-link').forEach(link => {
            link.addEventListener('click', () => {
                menu.remove();
            });
        });

        // Positioning logic
        const rect = triggerBtn.getBoundingClientRect();
        const scrollTop = window.pageYOffset || document.documentElement.scrollTop;
        const scrollLeft = window.pageXOffset || document.documentElement.scrollLeft;

        // Position it absolute relative to the body
        let top = rect.bottom + scrollTop + 6;
        let left = rect.right + scrollLeft - menu.offsetWidth;

        // Check if menu goes off screen bounds
        if (left < 10) left = 10;
        if (top + menu.offsetHeight > window.innerHeight + scrollTop) {
            top = rect.top + scrollTop - menu.offsetHeight - 6;
        }

        menu.style.top = `${top}px`;
        menu.style.left = `${left}px`;

        // Animation entry
        requestAnimationFrame(() => {
            menu.classList.add('show');
        });

        // Global outside click listener
        const closeMenu = (e) => {
            if (!menu.contains(e.target) && e.target !== triggerBtn && !triggerBtn.contains(e.target)) {
                menu.remove();
                document.removeEventListener('click', closeMenu);
            }
        };
        setTimeout(() => {
            document.addEventListener('click', closeMenu);
        }, 50);
    },

    // ── WebSocket Handler ──

    handleWSMessage(msg) {
        if (msg.type === 'position_update') {
            const ac = this.aircraft.find(a => a.id === msg.aircraft_id);
            const category = ac ? ac.category : 'plane';

            // Update map marker immediately
            FlightMap.updateMarker(msg.aircraft_id, {
                ...msg.data,
                tail_number: msg.tail_number,
                category: category,
            });

            // Patch in-memory position so any subsequent full re-render uses fresh data
            if (ac) {
                ac.latest_position = { ...(ac.latest_position || {}), ...msg.data };
            }

            // Update aircraft card telemetry in-place
            const card = document.querySelector(`.aircraft-card[data-id="${msg.aircraft_id}"]`);
            if (card) {
                card.style.borderColor = 'var(--accent)';
                setTimeout(() => { card.style.borderColor = ''; }, 1000);
                this._updateCardTelemetry(card, msg.data);
            }
        } else if (msg.type === 'flight_status') {
            Utils.toast(`Flight status: ${msg.old_status} → ${msg.new_status}`, 'info');
            this.loadAircraft();
            this.loadFlights();
        } else if (msg.type === 'tracker_status') {
            this._updateTrackerUI(msg);
        }
    },

    _updateCardTelemetry(card, pos) {
        const telemEl = card.querySelector('.ac-telemetry');
        if (!telemEl) return; // Card not in airborne state — structural changes handled by flight_status

        const altStr = pos.altitude_ft != null
            ? (pos.altitude_ft >= 18000
                ? `FL${Math.round(pos.altitude_ft / 100)}`
                : `${Math.round(pos.altitude_ft).toLocaleString()} ft`)
            : '—';

        const vals = telemEl.querySelectorAll('.ac-telem-val');
        if (vals.length >= 4) {
            vals[0].textContent = altStr;
            vals[1].textContent = Utils.formatSpeed(pos.ground_speed_kts);
            vals[2].textContent = Utils.formatHeading(pos.heading);
            vals[3].textContent = Utils.formatVRate(pos.vertical_rate_fpm);
        }

        const liveTime = telemEl.querySelector('.live-time-ago');
        if (liveTime && pos.timestamp) {
            liveTime.dataset.timestamp = pos.timestamp;
            liveTime.textContent = Utils.timeAgo(pos.timestamp);
        }

        const squawkEl = telemEl.querySelector('.ac-squawk');
        if (pos.squawk && !squawkEl) {
            const footer = telemEl.querySelector('.ac-telem-footer');
            if (footer) footer.insertAdjacentHTML('beforeend', `<span class="ac-squawk">Squawk ${pos.squawk}</span>`);
        } else if (!pos.squawk && squawkEl) {
            squawkEl.remove();
        } else if (squawkEl && pos.squawk) {
            squawkEl.textContent = `Squawk ${pos.squawk}`;
        }
    }
};
