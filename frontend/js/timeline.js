/**
 * Planey - Timeline Component
 * Dual-chart (altitude + speed) interactive flight data visualizer.
 * Hovering the chart shows a crosshair and updates the telemetry readout.
 * Clicking locks the position and moves the map marker to that point.
 */

const Timeline = {
    positions: [],
    _sorted: [],
    _altRange: { min: 0, max: 1 },
    _spdRange: { min: 0, max: 1 },
    currentIndex: 0,
    isPlaying: false,
    playInterval: null,
    flightId: null,
    aircraftId: null,
    _dragging: false,

    init() {
        document.getElementById('btn-close-timeline').addEventListener('click', () => this.hide());
        document.getElementById('btn-play-timeline').addEventListener('click', () => {
            if (this.isPlaying) this.stopPlay(); else this.startPlay();
        });
        this._initInteraction();
    },

    // ─── Data loading ──────────────────────────────────────────────────────────

    async showFlight(flightId, aircraftId, title) {
        try {
            const positions = await API.getFlightPositions(flightId);
            if (!positions || positions.length === 0) {
                Utils.toast('No position data for this flight', 'warning');
                return;
            }

            this.positions = positions;
            this.flightId = flightId;
            this.aircraftId = aircraftId;

            document.getElementById('timeline-title').textContent = title || 'Flight Timeline';
            document.getElementById('timeline-bar').style.display = 'block';

            FlightMap.drawTrail(aircraftId, positions);
            this._prepare();
            this._buildCharts();
            this._renderInfoBar(0);
            this._moveMapMarker(0);

            if (positions.length > 1) {
                const lats = positions.map(p => p.latitude);
                const lngs = positions.map(p => p.longitude);
                FlightMap.map.fitBounds([
                    [Math.min(...lats), Math.min(...lngs)],
                    [Math.max(...lats), Math.max(...lngs)]
                ], { padding: [50, 60] });
            }
        } catch (err) {
            Utils.toast('Failed to load flight positions', 'error');
            console.error(err);
        }
    },

    async showHistory(aircraftId, tailNumber, hours = 24) {
        try {
            const positions = await API.getPositionHistory(aircraftId, hours);
            if (!positions || positions.length === 0) {
                Utils.toast('No position history available', 'warning');
                return;
            }

            this.positions = positions;
            this.flightId = null;
            this.aircraftId = aircraftId;

            document.getElementById('timeline-title').textContent = `${tailNumber} — Last ${hours}h`;
            document.getElementById('timeline-bar').style.display = 'block';

            FlightMap.drawTrail(aircraftId, positions);
            this._prepare();
            this._buildCharts();
            this._renderInfoBar(0);
            this._moveMapMarker(0);

            if (positions.length > 1) {
                const lats = positions.map(p => p.latitude);
                const lngs = positions.map(p => p.longitude);
                FlightMap.map.fitBounds([
                    [Math.min(...lats), Math.min(...lngs)],
                    [Math.max(...lats), Math.max(...lngs)]
                ], { padding: [50, 60] });
            }
        } catch (err) {
            Utils.toast('Failed to load position history', 'error');
            console.error(err);
        }
    },

    hide() {
        document.getElementById('timeline-bar').style.display = 'none';
        this.stopPlay();

        const acId = this.aircraftId;
        this.positions = [];
        this._sorted = [];
        this.flightId = null;
        this.aircraftId = null;

        if (acId && window.Flights) {
            if (window.FlightMap) FlightMap.clearTrail(acId);
            window.API.pollAircraft(acId).then(pos => {
                if (pos) Flights.loadAircraft();
            }).catch(e => console.error('Failed to restore live position', e));
        }
    },

    // ─── Interaction ───────────────────────────────────────────────────────────

    _initInteraction() {
        const charts = document.getElementById('timeline-charts');
        if (!charts) return;

        charts.addEventListener('mousemove', (e) => this._onMove(e));
        charts.addEventListener('mouseleave', () => this._onLeave());
        charts.addEventListener('mousedown', (e) => {
            this._dragging = true;
            this._onChartClick(e);
        });
        charts.addEventListener('click', (e) => this._onChartClick(e));

        charts.addEventListener('touchmove', (e) => {
            e.preventDefault();
            this._onMove(e.touches[0]);
        }, { passive: false });
        charts.addEventListener('touchend', () => { this._dragging = false; });

        document.addEventListener('mousemove', (e) => {
            if (!this._dragging) return;
            this._onMove(e);
        });
        document.addEventListener('mouseup', () => { this._dragging = false; });
    },

    _idxFromEvent(e) {
        const svg = document.getElementById('chart-altitude');
        if (!svg || this._sorted.length === 0) return this.currentIndex;
        const rect = svg.getBoundingClientRect();
        const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
        return Math.round(pct * (this._sorted.length - 1));
    },

    _onMove(e) {
        if (this._sorted.length === 0) return;
        const idx = this._idxFromEvent(e);
        this._showCrosshair(idx);
        this._renderInfoBar(idx);
        if (this._dragging) {
            this.currentIndex = idx;
            this._moveMapMarker(idx);
            this._updateSelectedDots();
        }
    },

    _onLeave() {
        this._hideCrosshair();
        this._renderInfoBar(this.currentIndex);
    },

    _onChartClick(e) {
        if (this._sorted.length === 0) return;
        const idx = this._idxFromEvent(e);
        this.currentIndex = idx;
        this._moveMapMarker(idx);
        this._updateSelectedDots();
        this._renderInfoBar(idx);
    },

    // ─── Chart building ────────────────────────────────────────────────────────

    _prepare() {
        this._sorted = [...this.positions].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));
        this.currentIndex = 0;

        const alts = this._sorted.map(p => p.altitude_ft ?? 0);
        const spds = this._sorted.map(p => p.ground_speed_kts ?? 0);

        const rawAltMax = Math.max(...alts);
        const rawAltMin = Math.min(...alts);
        this._altRange = {
            min: Math.floor(rawAltMin / 1000) * 1000,
            max: Math.max(Math.ceil(rawAltMax / 1000) * 1000, 1000),
        };
        this._spdRange = {
            min: 0,
            max: Math.max(Math.ceil(Math.max(...spds) / 50) * 50, 50),
        };
    },

    _buildCharts() {
        const n = this._sorted.length;
        if (n === 0) return;

        this._renderAltChart();
        this._renderSpdChart();

        // Axis range labels
        const { min: aMin, max: aMax } = this._altRange;
        document.getElementById('alt-max-label').textContent =
            aMax >= 1000 ? `${(aMax / 1000).toFixed(0)}k ft` : `${aMax} ft`;
        document.getElementById('alt-min-label').textContent =
            aMin >= 1000 ? `${(aMin / 1000).toFixed(0)}k ft` : `${aMin} ft`;
        document.getElementById('spd-max-label').textContent = `${this._spdRange.max} kts`;

        // Time axis
        document.getElementById('tl-time-start').textContent = Utils.formatTime(this._sorted[0].timestamp);
        document.getElementById('tl-time-end').textContent = Utils.formatTime(this._sorted[n - 1].timestamp);
    },

    // SVG coordinate helpers (viewBox 0 0 1000 100)
    _x(i) {
        const n = this._sorted.length;
        return n <= 1 ? 500 : (i / (n - 1)) * 1000;
    },
    _yAlt(v) {
        const { min, max } = this._altRange;
        return 100 - ((v - min) / Math.max(max - min, 1)) * 85 - 7.5;
    },
    _ySpd(v) {
        const { min, max } = this._spdRange;
        return 100 - ((v - min) / Math.max(max - min, 1)) * 85 - 7.5;
    },

    _renderAltChart() {
        const n = this._sorted.length;
        const svg = document.getElementById('chart-altitude');

        // Gradient stops for area fill: color at each data point's x position
        const gradStops = this._sorted.map((p, i) => {
            const color = Utils.altitudeColor(p.altitude_ft ?? 0);
            const pct = ((this._x(i) / 1000) * 100).toFixed(2);
            return `<stop offset="${pct}%" stop-color="${color}" stop-opacity="0.25"/>`;
        }).join('');

        // Area fill path
        let area = `M 0,100 L ${f(this._x(0))},${f(this._yAlt(this._sorted[0].altitude_ft ?? 0))}`;
        for (let i = 1; i < n; i++) area += ` L ${f(this._x(i))},${f(this._yAlt(this._sorted[i].altitude_ft ?? 0))}`;
        area += ' L 1000,100 Z';

        // Altitude-colored line segments (matches the map trail)
        let lines = '';
        for (let i = 1; i < n; i++) {
            const avg = ((this._sorted[i - 1].altitude_ft ?? 0) + (this._sorted[i].altitude_ft ?? 0)) / 2;
            lines += `<line x1="${f(this._x(i-1))}" y1="${f(this._yAlt(this._sorted[i-1].altitude_ft??0))}" ` +
                     `x2="${f(this._x(i))}" y2="${f(this._yAlt(this._sorted[i].altitude_ft??0))}" ` +
                     `stroke="${Utils.altitudeColor(avg)}" stroke-width="1.5" stroke-linecap="round"/>`;
        }

        // Subtle horizontal gridlines
        const { min: aMin, max: aMax } = this._altRange;
        const gridVals = [aMin, (aMin + aMax) / 2, aMax];
        const grid = gridVals.map(v =>
            `<line x1="0" y1="${f(this._yAlt(v))}" x2="1000" y2="${f(this._yAlt(v))}" stroke="rgba(255,255,255,0.06)" stroke-width="0.8" stroke-dasharray="4,4"/>`
        ).join('');

        const ix = f(this._x(0)), iy = f(this._yAlt(this._sorted[0].altitude_ft ?? 0));

        svg.innerHTML = `
            <defs>
                <linearGradient id="altFillGrad" gradientUnits="userSpaceOnUse" x1="0" y1="0" x2="1000" y2="0">
                    ${gradStops}
                </linearGradient>
            </defs>
            <g>${grid}</g>
            <path d="${area}" fill="url(#altFillGrad)"/>
            <g>${lines}</g>
            <line class="tl-xhair" x1="-9999" y1="0" x2="-9999" y2="100" stroke="rgba(255,255,255,0.65)" stroke-width="0.7"/>
            <circle class="tl-xhair-dot" cx="-9999" cy="50" r="3" fill="#fff" stroke="rgba(0,0,0,0.5)" stroke-width="0.8"/>
            <circle id="alt-sel-dot" cx="${ix}" cy="${iy}" r="3.5" fill="var(--accent)" stroke="#fff" stroke-width="1.2"/>
        `;
    },

    _renderSpdChart() {
        const n = this._sorted.length;
        const svg = document.getElementById('chart-speed');

        let area = `M 0,100 L ${f(this._x(0))},${f(this._ySpd(this._sorted[0].ground_speed_kts ?? 0))}`;
        for (let i = 1; i < n; i++) area += ` L ${f(this._x(i))},${f(this._ySpd(this._sorted[i].ground_speed_kts ?? 0))}`;
        area += ' L 1000,100 Z';

        let line = `M ${f(this._x(0))},${f(this._ySpd(this._sorted[0].ground_speed_kts ?? 0))}`;
        for (let i = 1; i < n; i++) line += ` L ${f(this._x(i))},${f(this._ySpd(this._sorted[i].ground_speed_kts ?? 0))}`;

        const { max: sMax } = this._spdRange;
        const gridVals = [0, sMax / 2, sMax];
        const grid = gridVals.map(v =>
            `<line x1="0" y1="${f(this._ySpd(v))}" x2="1000" y2="${f(this._ySpd(v))}" stroke="rgba(255,255,255,0.06)" stroke-width="0.8" stroke-dasharray="4,4"/>`
        ).join('');

        const ix = f(this._x(0)), iy = f(this._ySpd(this._sorted[0].ground_speed_kts ?? 0));

        svg.innerHTML = `
            <defs>
                <linearGradient id="spdFillGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stop-color="#00d4ff" stop-opacity="0.3"/>
                    <stop offset="100%" stop-color="#00d4ff" stop-opacity="0.03"/>
                </linearGradient>
            </defs>
            <g>${grid}</g>
            <path d="${area}" fill="url(#spdFillGrad)"/>
            <path d="${line}" fill="none" stroke="#00d4ff" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
            <line class="tl-xhair" x1="-9999" y1="0" x2="-9999" y2="100" stroke="rgba(255,255,255,0.65)" stroke-width="0.7"/>
            <circle class="tl-xhair-dot" cx="-9999" cy="50" r="3" fill="#fff" stroke="rgba(0,0,0,0.5)" stroke-width="0.8"/>
            <circle id="spd-sel-dot" cx="${ix}" cy="${iy}" r="3.5" fill="var(--accent)" stroke="#fff" stroke-width="1.2"/>
        `;
    },

    // ─── Crosshair & selected dot ──────────────────────────────────────────────

    _showCrosshair(idx) {
        const xSvg = this._x(idx);
        const pos = this._sorted[idx];
        if (!pos) return;

        const altSvg = document.getElementById('chart-altitude');
        if (altSvg) {
            const xh = altSvg.querySelector('.tl-xhair');
            const dot = altSvg.querySelector('.tl-xhair-dot');
            const yv = f(this._yAlt(pos.altitude_ft ?? 0));
            if (xh) { xh.setAttribute('x1', xSvg); xh.setAttribute('x2', xSvg); }
            if (dot) { dot.setAttribute('cx', xSvg); dot.setAttribute('cy', yv); }
        }

        const spdSvg = document.getElementById('chart-speed');
        if (spdSvg) {
            const xh = spdSvg.querySelector('.tl-xhair');
            const dot = spdSvg.querySelector('.tl-xhair-dot');
            const yv = f(this._ySpd(pos.ground_speed_kts ?? 0));
            if (xh) { xh.setAttribute('x1', xSvg); xh.setAttribute('x2', xSvg); }
            if (dot) { dot.setAttribute('cx', xSvg); dot.setAttribute('cy', yv); }
        }
    },

    _hideCrosshair() {
        [document.getElementById('chart-altitude'), document.getElementById('chart-speed')].forEach(svg => {
            if (!svg) return;
            svg.querySelector('.tl-xhair')?.setAttribute('x1', '-9999');
            svg.querySelector('.tl-xhair')?.setAttribute('x2', '-9999');
            svg.querySelector('.tl-xhair-dot')?.setAttribute('cx', '-9999');
        });
    },

    _updateSelectedDots() {
        if (this._sorted.length === 0) return;
        const xSvg = this._x(this.currentIndex);
        const pos = this._sorted[this.currentIndex];

        const altDot = document.getElementById('alt-sel-dot');
        if (altDot) { altDot.setAttribute('cx', xSvg); altDot.setAttribute('cy', f(this._yAlt(pos.altitude_ft ?? 0))); }

        const spdDot = document.getElementById('spd-sel-dot');
        if (spdDot) { spdDot.setAttribute('cx', xSvg); spdDot.setAttribute('cy', f(this._ySpd(pos.ground_speed_kts ?? 0))); }
    },

    // ─── Map & info updates ────────────────────────────────────────────────────

    _moveMapMarker(idx) {
        if (!this.aircraftId || !FlightMap.markers[this.aircraftId]) return;
        const pos = this._sorted[idx];
        if (!pos) return;
        const color = Utils.altitudeColor(pos.altitude_ft);
        FlightMap.markers[this.aircraftId].setLatLng([pos.latitude, pos.longitude]);
        FlightMap.markers[this.aircraftId].setIcon(Utils.airplaneIcon(pos.heading, color));
    },

    _renderInfoBar(idx) {
        if (this._sorted.length === 0) return;
        const pos = this._sorted[Math.max(0, Math.min(idx, this._sorted.length - 1))];

        document.getElementById('timeline-time').textContent = Utils.formatDateTimeSecs(pos.timestamp);

        const altEl = document.getElementById('timeline-alt');
        altEl.textContent = Utils.formatAlt(pos.altitude_ft);
        altEl.style.color = Utils.altitudeColor(pos.altitude_ft);

        document.getElementById('timeline-speed').textContent = Utils.formatSpeed(pos.ground_speed_kts);

        const hdgEl = document.getElementById('timeline-heading');
        const compass = Utils.compassDirection(pos.heading);
        hdgEl.textContent = pos.heading != null
            ? `${Math.round(pos.heading)}°${compass ? ' ' + compass : ''}`
            : '—';

        const vrEl = document.getElementById('timeline-vrate');
        const vr = pos.vertical_rate_fpm;
        vrEl.textContent = Utils.formatVRate(vr);
        vrEl.style.color = vr == null ? '' : (vr > 200 ? 'var(--green)' : vr < -200 ? 'var(--red)' : '');
    },

    // ─── Playback ──────────────────────────────────────────────────────────────

    startPlay() {
        if (this.isPlaying || this._sorted.length === 0) return;
        if (this.currentIndex >= this._sorted.length - 1) this.currentIndex = 0;
        this.isPlaying = true;
        this._setPlayIcon(true);
        this.playInterval = setInterval(() => {
            if (this.currentIndex >= this._sorted.length - 1) {
                this.stopPlay();
                return;
            }
            this.currentIndex++;
            this._moveMapMarker(this.currentIndex);
            this._updateSelectedDots();
            this._showCrosshair(this.currentIndex);
            this._renderInfoBar(this.currentIndex);
        }, 200);
    },

    stopPlay() {
        this.isPlaying = false;
        if (this.playInterval) { clearInterval(this.playInterval); this.playInterval = null; }
        this._setPlayIcon(false);
    },

    _setPlayIcon(playing) {
        const icon = document.getElementById('icon-play-pause');
        if (!icon) return;
        icon.innerHTML = playing
            ? '<rect x="6" y="4" width="4" height="16"/><rect x="14" y="4" width="4" height="16"/>'
            : '<polygon points="5 3 19 12 5 21 5 3"/>';
    },

    // ─── Legacy compat ─────────────────────────────────────────────────────────

    seekTo(pct) {
        if (this._sorted.length === 0) return;
        this.currentIndex = Math.round(pct * (this._sorted.length - 1));
        this._moveMapMarker(this.currentIndex);
        this._updateSelectedDots();
        this._renderInfoBar(this.currentIndex);
    },
};

// Compact number formatter for SVG coordinates
function f(n) { return n.toFixed(2); }
