/**
 * Planey - Map Component
 * Leaflet map with altitude-colored flight paths and animated aircraft markers
 */

const FlightMap = {
    map: null,
    markers: {},       // aircraft_id → L.marker
    trails: {},        // aircraft_id → L.polyline array (segments)
    plannedRoutes: {}, // aircraft_id → L.polyline (dashed route)
    airportMarkers: {}, // iata → L.circleMarker
    showTrails: true,
    _tileLayers: null,
    _labelsOverlay: null,
    _currentLayerName: 'dark',
    _currentProvider: 'carto',

    _providers: {
        carto: {
            dark: {
                url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
                opts: { maxZoom: 19, subdomains: 'abcd' },
                attribution: '© <a href="https://carto.com/">CARTO</a> · © <a href="https://www.openstreetmap.org/copyright">OSM</a>'
            },
            light: {
                url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
                opts: { maxZoom: 19, subdomains: 'abcd' },
                attribution: '© <a href="https://carto.com/">CARTO</a> · © <a href="https://www.openstreetmap.org/copyright">OSM</a>'
            }
        },
        esri: {
            dark: {
                url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Dark_Gray_Canvas/MapServer/tile/{z}/{y}/{x}',
                opts: { maxZoom: 16 },
                attribution: '© <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, © OSM contributors'
            },
            light: {
                url: 'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Canvas/MapServer/tile/{z}/{y}/{x}',
                opts: { maxZoom: 16 },
                attribution: '© <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, © OSM contributors'
            }
        },
        osm: {
            // OSM has no dark mode — fall back to CARTO dark for that view
            dark: {
                url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
                opts: { maxZoom: 19, subdomains: 'abcd' },
                attribution: '© <a href="https://carto.com/">CARTO</a> · © <a href="https://www.openstreetmap.org/copyright">OSM</a>'
            },
            light: {
                url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
                opts: { maxZoom: 19 },
                attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors'
            }
        }
    },

    _satelliteDef: {
        url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
        opts: { maxZoom: 19 },
        attribution: '© <a href="https://www.esri.com/">Esri</a> · <a href="https://opensky-network.org">OpenSky</a>'
    },

    _layerDefs: null,

    init() {
        this.map = L.map('map', {
            center: [39.8283, -98.5795], // Center US
            zoom: 4,
            zoomControl: true,
            attributionControl: false
        });

        // Restore saved provider preference
        const savedProvider = localStorage.getItem('mapProvider');
        if (savedProvider && this._providers[savedProvider]) {
            this._currentProvider = savedProvider;
        }

        // Build layer definitions for current provider
        this._layerDefs = this._buildLayerDefs();

        // Build tile layer instances
        this._tileLayers = {};
        for (const [name, def] of Object.entries(this._layerDefs)) {
            this._tileLayers[name] = L.tileLayer(def.url, def.opts);
        }
        this._tileLayers.dark.addTo(this.map);

        // Transparent labels overlay for satellite view (towns, rivers, airports, POIs)
        this._labelsOverlay = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
            { maxZoom: 19, opacity: 1 }
        );

        // Attribution control (updated on layer switch)
        this._attribution = L.control.attribution({ prefix: false, position: 'bottomleft' });
        this._attribution.addTo(this.map);
        this._attribution.addAttribution(this._layerDefs.dark.attribution);

        // Controls
        document.getElementById('btn-center-all').addEventListener('click', () => this.updateDefaultMapView());
        document.getElementById('btn-toggle-trails').addEventListener('click', (e) => {
            this.showTrails = !this.showTrails;
            e.currentTarget.classList.toggle('active', this.showTrails);
            this._toggleTrails();
        });

        // Layer switcher buttons
        document.querySelectorAll('.map-layer-btn').forEach(btn => {
            btn.addEventListener('click', () => {
                const layer = btn.dataset.layer;
                if (layer === this._currentLayerName) return;
                this.setLayer(layer);
                document.querySelectorAll('.map-layer-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            });
        });

        // Provider switcher buttons — mark saved preference active on load
        document.querySelectorAll('.map-provider-btn').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.provider === this._currentProvider);
            btn.addEventListener('click', () => {
                const provider = btn.dataset.provider;
                if (provider === this._currentProvider) return;
                this.setProvider(provider);
                document.querySelectorAll('.map-provider-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            });
        });
    },

    setLayer(name) {
        if (!this._tileLayers[name]) return;
        this._tileLayers[this._currentLayerName].remove();
        this._currentLayerName = name;
        this._tileLayers[name].addTo(this.map);

        // Add/remove the labels overlay for satellite (dark/light have built-in labels)
        const isSatellite = name === 'satellite';
        if (isSatellite) {
            this._labelsOverlay.addTo(this.map);
        } else {
            this._labelsOverlay.remove();
        }

        // Satellite view: lighten trail colors so they read against imagery
        Object.values(this.trails).forEach(segs =>
            segs.forEach(seg => seg.setStyle({ weight: isSatellite ? 3 : 4, opacity: isSatellite ? 0.95 : 0.8 }))
        );
    },

    _buildLayerDefs() {
        const p = this._providers[this._currentProvider];
        return {
            dark: p.dark,
            light: p.light,
            satellite: this._satelliteDef
        };
    },

    setProvider(name) {
        if (!this._providers[name] || name === this._currentProvider) return;
        this._currentProvider = name;
        localStorage.setItem('mapProvider', name);

        const p = this._providers[name];
        const onDarkOrLight = this._currentLayerName === 'dark' || this._currentLayerName === 'light';

        if (onDarkOrLight) {
            this._tileLayers[this._currentLayerName].remove();
        }

        // Rebuild dark/light tile layers for the new provider
        this._tileLayers.dark = L.tileLayer(p.dark.url, p.dark.opts);
        this._tileLayers.light = L.tileLayer(p.light.url, p.light.opts);
        this._layerDefs = this._buildLayerDefs();

        if (onDarkOrLight) {
            this._tileLayers[this._currentLayerName].addTo(this.map);
        }
    },

    /**
     * Update or create a marker for an aircraft
     */
    updateMarker(aircraftId, data) {
        const { latitude, longitude, heading, altitude_ft, tail_number, ground_speed_kts, vertical_rate_fpm, on_ground, category } = data;
        if (latitude == null || longitude == null) return;

        const pos = [latitude, longitude];
        const color = Utils.altitudeColor(altitude_ft);

        if (this.markers[aircraftId]) {
            // Animate move
            this.markers[aircraftId].setLatLng(pos);
            this.markers[aircraftId].setIcon(Utils.aircraftIcon(heading, color, category));
        } else {
            // Create new marker
            const marker = L.marker(pos, {
                icon: Utils.aircraftIcon(heading, color, category),
                zIndexOffset: 1000
            }).addTo(this.map);

            // Popup content
            marker.bindPopup('', { className: 'flight-popup', maxWidth: 260 });
            marker.on('click', () => {
                marker.setPopupContent(this._popupHtml(data));
            });

            // Tooltip (Hover)
            marker.bindTooltip(this._tooltipHtml(data), {
                className: 'trail-tooltip',
                direction: 'top',
                offset: [0, -15],
                sticky: false
            });

            this.markers[aircraftId] = marker;
        }

        // Update popup/tooltip if open
        const marker = this.markers[aircraftId];
        if (marker.isPopupOpen()) {
            marker.setPopupContent(this._popupHtml(data));
        }
        if (marker.getTooltip()) {
            marker.setTooltipContent(this._tooltipHtml(data));
        }

        // Add trail segment
        if (this.showTrails) {
            this._addTrailPoint(aircraftId, pos, data);
        }
    },

    /**
     * Draw a complete trail from position history
     */
    drawTrail(aircraftId, positions) {
        this.clearTrail(aircraftId);
        if (!positions || positions.length < 2) return;

        // API returns DESC (newest first), we need oldest first to build segments correctly
        const sorted = [...positions].sort((a, b) => new Date(a.timestamp) - new Date(b.timestamp));

        const segments = [];
        for (let i = 1; i < sorted.length; i++) {
            const p1 = sorted[i - 1];
            const p2 = sorted[i];
            
            // Skip if gap is too large (> 60 mins) to allow connecting across coverage gaps
            const gap = (new Date(p2.timestamp) - new Date(p1.timestamp)) / 1000 / 60;
            if (gap > 60) continue;

            const color = Utils.altitudeColor((p1.altitude_ft + (p2.altitude_ft || 0)) / 2);
            const line = L.polyline(
                [[p1.latitude, p1.longitude], [p2.latitude, p2.longitude]],
                { color, weight: 4, opacity: 0.8 }
            );
            
            line.bindTooltip(this._trailTooltipHtml(p2), {
                className: 'trail-tooltip',
                direction: 'top',
                sticky: true,
                offset: [0, -5]
            });

            if (this.showTrails) line.addTo(this.map);
            segments.push(line);
        }
        this.trails[aircraftId] = segments;
    },

    /**
     * Add a single trail point (for real-time updates)
     */
    _addTrailPoint(aircraftId, pos, posData) {
        if (!this.trails[aircraftId]) this.trails[aircraftId] = [];
        const segments = this.trails[aircraftId];
        const altFt = posData?.altitude_ft;

        if (segments.length > 0) {
            const last = segments[segments.length - 1];
            const latLngs = last.getLatLngs();
            const lastPos = latLngs[latLngs.length - 1];

            // Prevent drawing a line if the jump is too large (likely a stale position or new flight)
            // Increased to 500km to allow for OpenSky coverage gaps before fallback kicks in
            const dist = L.latLng(lastPos).distanceTo(L.latLng(pos));
            if (dist > 500000) { // 500km
                console.log(`[Map] Large jump detected for ${aircraftId} (${Math.round(dist/1000)}km), starting new segment`);
                return;
            }

            const color = Utils.altitudeColor(altFt);
            const seg = L.polyline([lastPos, pos], { color, weight: 4, opacity: 0.8 });

            seg.bindTooltip(this._trailTooltipHtml(posData || { altitude_ft: altFt, timestamp: new Date().toISOString() }), {
                className: 'trail-tooltip',
                direction: 'top',
                sticky: true,
                offset: [0, -5]
            });

            seg.addTo(this.map);
            segments.push(seg);
        } else {
            // Store first point, wait for next
            const placeholder = L.polyline([pos, pos], { opacity: 0 });
            segments.push(placeholder);
        }
    },

    clearTrail(aircraftId) {
        if (this.trails[aircraftId]) {
            this.trails[aircraftId].forEach(s => s.remove());
            delete this.trails[aircraftId];
        }
    },

    /**
     * Draw a dashed straight line representing the planned route
     */
    drawPlannedRoute(aircraftId, flight) {
        // Clear existing planned route if any
        if (this.plannedRoutes[aircraftId]) {
            this.plannedRoutes[aircraftId].remove();
            delete this.plannedRoutes[aircraftId];
        }

        if (!flight || flight.status !== 'scheduled') return;
        if (!flight.departure_lat || !flight.departure_lon || !flight.arrival_lat || !flight.arrival_lon) return;

        const dep = [flight.departure_lat, flight.departure_lon];
        const arr = [flight.arrival_lat, flight.arrival_lon];

        const line = L.polyline([dep, arr], {
            color: '#a0a0a0',
            weight: 2,
            opacity: 0.6,
            dashArray: '10, 10'
        });

        line.bindTooltip(`Planned: ${flight.departure_iata || '?'} → ${flight.arrival_iata || '?'}`, {
            className: 'trail-tooltip',
            direction: 'center',
            sticky: true
        });

        line.addTo(this.map);
        this.plannedRoutes[aircraftId] = line;
    },

    removeMarker(aircraftId) {
        if (this.markers[aircraftId]) {
            this.markers[aircraftId].remove();
            delete this.markers[aircraftId];
        }
        this.clearTrail(aircraftId);
    },

    /**
     * Add airport markers for departure/arrival
     */
    addAirportMarker(iata, lat, lng, name) {
        if (this.airportMarkers[iata]) return;
        if (!lat || !lng) return;

        const marker = L.circleMarker([lat, lng], {
            radius: 5, fillColor: '#a78bfa', fillOpacity: 0.8,
            color: '#a78bfa', weight: 1, opacity: 0.6
        }).addTo(this.map);

        marker.bindTooltip(`${iata} — ${name || ''}`, {
            className: 'airport-tooltip', direction: 'top', offset: [0, -8]
        });

        this.airportMarkers[iata] = marker;
    },

    fitAllMarkers() {
        this.updateDefaultMapView();
    },

    focusAircraft(aircraftId) {
        const m = this.markers[aircraftId];
        if (!m) return;

        // Check if there is a track/trail for this aircraft
        const segments = this.trails[aircraftId];
        const latLngs = [];
        if (segments && segments.length > 0) {
            segments.forEach(seg => {
                const pts = seg.getLatLngs();
                pts.forEach(p => {
                    if (Array.isArray(p)) {
                        p.forEach(subP => latLngs.push(L.latLng(subP)));
                    } else {
                        latLngs.push(L.latLng(p));
                    }
                });
            });
        }

        // Add the marker position as well
        latLngs.push(m.getLatLng());

        if (latLngs.length > 1) {
            // Fit bounds to cover the track
            const bounds = L.latLngBounds(latLngs);
            this.map.flyToBounds(bounds.pad(0.15), { duration: 1.2 });
        } else {
            // No track (only the aircraft marker), zoom to US state level (zoom 7)
            this.map.flyTo(m.getLatLng(), 7, { duration: 1.2 });
        }
    },

    /**
     * Center and zoom the map to cover all active tracks.
     * If there are no tracks, center on the last known position at a US state level (zoom 7).
     * If there are no markers or tracks, keep default US center.
     */
    updateDefaultMapView() {
        const latLngs = [];
        
        // 1. Gather all coordinates from all active trails
        Object.values(this.trails).forEach(segments => {
            segments.forEach(seg => {
                const pts = seg.getLatLngs();
                pts.forEach(p => {
                    if (Array.isArray(p)) {
                        p.forEach(subP => latLngs.push(L.latLng(subP)));
                    } else {
                        latLngs.push(L.latLng(p));
                    }
                });
            });
        });

        if (latLngs.length > 1) {
            // Fit bounds to cover all current tracks
            const bounds = L.latLngBounds(latLngs);
            this.map.fitBounds(bounds.pad(0.15));
            console.log("[Map] Zoomed and centered to cover current tracks.");
            return;
        }

        // 2. If no tracks, look for last known aircraft positions (markers)
        const markerLatLngs = [];
        Object.values(this.markers).forEach(marker => {
            markerLatLngs.push(marker.getLatLng());
        });

        if (markerLatLngs.length > 0) {
            // Focus on the first/primary or selected aircraft
            let targetLatLng = markerLatLngs[0];
            if (window.Flights && Flights.selectedAircraftId && this.markers[Flights.selectedAircraftId]) {
                targetLatLng = this.markers[Flights.selectedAircraftId].getLatLng();
            }
            // State-level zoom (7)
            this.map.setView(targetLatLng, 7);
            console.log("[Map] No tracks found. Centered on last known position at US state level (zoom 7).");
        } else {
            console.log("[Map] No tracks or active positions found. Map kept at default US view.");
        }
    },

    _toggleTrails() {
        Object.values(this.trails).forEach(segments => {
            segments.forEach(s => {
                if (this.showTrails) s.addTo(this.map);
                else s.remove();
            });
        });
    },

    _popupHtml(data) {
        return `
            <div style="min-width:200px">
                <div style="font-size:15px;font-weight:700;color:#00d4ff;margin-bottom:6px">
                    ${data.tail_number || 'Unknown'}${data.category === 'helicopter' ? ' 🚁' : ''}
                    ${data.flight_number ? `<span style="font-size:12px;color:#8899aa;margin-left:6px">${data.flight_number}</span>` : ''}
                </div>
                ${data.departure_iata || data.arrival_iata ? `
                    <div style="font-size:14px;margin-bottom:8px;display:flex;align-items:center;gap:6px">
                        <strong>${data.departure_iata || '???'}</strong>
                        <span style="color:#556677">→</span>
                        <strong>${data.arrival_iata || '???'}</strong>
                    </div>
                ` : ''}
                <div style="display:grid;grid-template-columns:1fr 1fr;gap:4px;font-size:12px">
                    <div><span style="color:#556677">Alt:</span> ${Utils.formatAlt(data.altitude_ft)}</div>
                    <div><span style="color:#556677">Spd:</span> ${Utils.formatSpeed(data.ground_speed_kts)}</div>
                    <div><span style="color:#556677">Hdg:</span> ${Utils.formatHeading(data.heading)}</div>
                    <div><span style="color:#556677">VS:</span> ${Utils.formatVRate(data.vertical_rate_fpm)}</div>
                </div>
                <div style="font-size:10px;color:#556677;margin-top:6px">
                    ${data.timestamp ? `<span class="live-time-ago" data-timestamp="${data.timestamp}">${Utils.timeAgo(data.timestamp)}</span>` : ''}
                </div>
            </div>
        `;
    },

    _trailTooltipHtml(pos, timeLabel = null) {
        const phase = Utils.flightPhase(pos.vertical_rate_fpm, pos.on_ground);
        const fl = Utils.flightLevel(pos.altitude_ft);
        const altColor = Utils.altitudeColor(pos.altitude_ft);
        const compass = Utils.compassDirection(pos.heading);

        const altHtml = fl
            ? `<div class="tt-value" style="color:${altColor}">${fl}</div><div class="tt-sub">${Utils.formatAlt(pos.altitude_ft)}</div>`
            : `<div class="tt-value" style="color:${altColor}">${Utils.formatAlt(pos.altitude_ft)}</div>`;

        const vr = pos.vertical_rate_fpm;
        const vrDisplay = vr != null ? (vr > 0 ? `+${Math.round(vr).toLocaleString()}` : Math.round(vr).toLocaleString()) : '—';
        const vrCls = vr == null ? '' : (vr > 200 ? 'tt-vs-up' : (vr < -200 ? 'tt-vs-down' : 'tt-vs-level'));

        const squawk = pos.squawk;
        const emergencyLabels = { '7500': 'HIJACK', '7600': 'RADIO FAIL', '7700': 'EMERGENCY' };
        const squawkHtml = squawk && squawk !== '0000' ? `
            <div class="tt-squawk-row">
                <span class="tt-label">SQUAWK</span>
                <span class="tt-squawk-val${emergencyLabels[squawk] ? ' tt-squawk-emerg' : squawk === '1200' ? ' tt-squawk-vfr' : ''}">
                    ${squawk}${emergencyLabels[squawk] ? ' ⚠ ' + emergencyLabels[squawk] : squawk === '1200' ? ' VFR' : ''}
                </span>
            </div>` : '';

        const timeHtml = timeLabel !== null
            ? timeLabel
            : Utils.formatDateTimeSecs(pos.timestamp);

        return `<div class="track-tooltip">
            <div class="tt-header">
                <span class="tt-time">${timeHtml}</span>
                <span class="tt-phase ${phase.cls}">${phase.arrow}${phase.arrow ? ' ' : ''}${phase.label}</span>
            </div>
            <div class="tt-grid">
                <div class="tt-cell">
                    <div class="tt-label">ALTITUDE</div>
                    ${altHtml}
                </div>
                <div class="tt-cell">
                    <div class="tt-label">SPEED</div>
                    <div class="tt-value">${pos.ground_speed_kts != null ? Math.round(pos.ground_speed_kts) : '—'}<span class="tt-unit">kts</span></div>
                </div>
                <div class="tt-cell">
                    <div class="tt-label">COURSE</div>
                    <div class="tt-value">${pos.heading != null ? Math.round(pos.heading) + '°' : '—'}</div>
                    ${compass ? `<div class="tt-sub">${compass}</div>` : ''}
                </div>
                <div class="tt-cell">
                    <div class="tt-label">VERT SPEED</div>
                    <div class="tt-value ${vrCls}">${vrDisplay}</div>
                    ${vr != null ? '<div class="tt-sub">ft/min</div>' : ''}
                </div>
            </div>
            ${squawkHtml}
        </div>`;
    },

    _tooltipHtml(data) {
        const timeLabel = data.timestamp
            ? `<span class="live-time-ago" data-timestamp="${data.timestamp}">${Utils.timeAgo(data.timestamp)}</span>`
            : '—';
        return this._trailTooltipHtml(data, timeLabel);
    }
};
