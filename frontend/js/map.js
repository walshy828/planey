/**
 * Planey - Map Component
 * Leaflet map with altitude-colored flight paths and animated aircraft markers
 */

const FlightMap = {
    map: null,
    markers: {},
    trails: {},
    plannedRoutes: {},
    airportMarkers: {},
    showTrails: true,
    _tileLayer: null,
    _labelsOverlay: null,
    _currentPreset: 'street',
    _airspaceLayers: [],
    _showAirspace: false,
    _openaipKey: '',

    _presets: {
        night: {
            url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
            opts: { maxZoom: 19, subdomains: 'abcd' },
            attribution: '© <a href="https://carto.com/">CARTO</a> · © <a href="https://www.openstreetmap.org/copyright">OSM</a>',
            satellite: false,
        },
        street: {
            url: 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png',
            opts: { maxZoom: 19 },
            attribution: '© <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
            satellite: false,
        },
        terrain: {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Topo_Map/MapServer/tile/{z}/{y}/{x}',
            opts: { maxZoom: 19 },
            attribution: '© <a href="https://www.esri.com/">Esri</a>, HERE, Garmin, USGS, NGA, EPA, NPS',
            satellite: false,
        },
        satellite: {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            opts: { maxZoom: 19 },
            attribution: '© <a href="https://www.esri.com/">Esri</a> · <a href="https://opensky-network.org">OpenSky</a>',
            satellite: true,
        },
        sectional: {
            url: 'https://tiles.arcgis.com/tiles/ssFJjBXIUyZDrSYZ/arcgis/rest/services/VFR_Sectional/MapServer/tile/{z}/{y}/{x}',
            opts: { maxZoom: 14, minNativeZoom: 8, maxNativeZoom: 12 },
            attribution: '© <a href="https://www.faa.gov/">FAA</a> VFR Sectional Charts',
            satellite: false,
        },
    },

    async init() {
        this.map = L.map('map', {
            center: [39.8283, -98.5795],
            zoom: 4,
            zoomControl: true,
            attributionControl: false
        });

        // Restore saved preset; default to 'street'
        const saved = localStorage.getItem('mapPreset');
        if (saved && this._presets[saved]) this._currentPreset = saved;

        // Build single tile layer from preset
        const preset = this._presets[this._currentPreset];
        this._tileLayer = L.tileLayer(preset.url, preset.opts);
        this._tileLayer.addTo(this.map);

        // Transparent labels overlay for satellite view
        this._labelsOverlay = L.tileLayer(
            'https://server.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}',
            { maxZoom: 19, opacity: 1 }
        );
        if (preset.satellite) this._labelsOverlay.addTo(this.map);

        // Attribution control
        this._attribution = L.control.attribution({ prefix: false, position: 'bottomleft' });
        this._attribution.addTo(this.map);
        this._currentAttribution = preset.attribution;
        this._attribution.addAttribution(preset.attribution);

        // Action buttons
        document.getElementById('btn-center-all').addEventListener('click', () => this.updateDefaultMapView());
        document.getElementById('btn-toggle-trails').addEventListener('click', (e) => {
            this.showTrails = !this.showTrails;
            e.currentTarget.classList.toggle('active', this.showTrails);
            this._toggleTrails();
        });

        // Preset style buttons
        document.querySelectorAll('.map-style-btn[data-preset]').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.preset === this._currentPreset);
            btn.addEventListener('click', () => {
                const name = btn.dataset.preset;
                if (name === this._currentPreset) return;
                this.setPreset(name);
                document.querySelectorAll('.map-style-btn').forEach(b => b.classList.remove('active'));
                btn.classList.add('active');
            });
        });

        // Airspace overlay toggle
        document.getElementById('btn-toggle-airspace').addEventListener('click', (e) => {
            this._showAirspace = !this._showAirspace;
            e.currentTarget.classList.toggle('active', this._showAirspace);
            localStorage.setItem('mapAirspace', this._showAirspace ? '1' : '0');
            this._applyAirspaceLayers();
        });

        // Fetch OpenAIP key and conditionally show the airspace button
        try {
            const resp = await fetch('/api/config');
            const cfg = await resp.json();
            if (cfg.openaip_api_key) {
                this._openaipKey = cfg.openaip_api_key;
                this._buildAirspaceLayers();
                document.getElementById('btn-toggle-airspace').style.display = '';

                if (localStorage.getItem('mapAirspace') === '1') {
                    this._showAirspace = true;
                    document.getElementById('btn-toggle-airspace').classList.add('active');
                    this._applyAirspaceLayers();
                }
            }
        } catch (e) {
            console.warn('[Map] Could not load OpenAIP config:', e);
        }
    },

    _buildAirspaceLayers() {
        const key = this._openaipKey;
        this._airspaceLayers = [
            L.tileLayer(`https://{s}.api.tiles.openaip.net/api/data/openaip/{z}/{x}/{y}.png?apiKey=${key}`, {
                subdomains: 'abc', minZoom: 7, maxZoom: 17, opacity: 0.8, tileSize: 256
            }),
        ];
    },

    _applyAirspaceLayers() {
        this._airspaceLayers.forEach(layer => {
            if (this._showAirspace) layer.addTo(this.map);
            else layer.remove();
        });
    },

    setPreset(name) {
        const preset = this._presets[name];
        if (!preset) return;

        this._tileLayer.remove();
        this._labelsOverlay.remove();

        this._currentPreset = name;
        localStorage.setItem('mapPreset', name);

        this._tileLayer = L.tileLayer(preset.url, preset.opts);
        this._tileLayer.addTo(this.map);

        if (preset.satellite) this._labelsOverlay.addTo(this.map);

        this._attribution.removeAttribution(this._currentAttribution);
        this._attribution.addAttribution(preset.attribution);
        this._currentAttribution = preset.attribution;

        // Adjust trail weight/opacity for satellite vs vector
        Object.values(this.trails).forEach(segs =>
            segs.forEach(seg => seg.setStyle({ weight: preset.satellite ? 3 : 4, opacity: preset.satellite ? 0.95 : 0.8 }))
        );

        // Re-add airspace layers on top of the new base layer
        if (this._showAirspace) {
            this._airspaceLayers.forEach(l => { l.remove(); l.addTo(this.map); });
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
     * Draw a dashed straight line to the destination.
     * - Scheduled flights: departure → arrival
     * - Active flights: current position → arrival (updates as aircraft moves)
     */
    drawPlannedRoute(aircraftId, flight, currentPos = null) {
        if (this.plannedRoutes[aircraftId]) {
            this.plannedRoutes[aircraftId].remove();
            delete this.plannedRoutes[aircraftId];
        }

        if (!flight || !flight.arrival_lat || !flight.arrival_lon) return;

        const isScheduled = flight.status === 'scheduled';
        const isActive = flight.status === 'active';
        if (!isScheduled && !isActive) return;

        let startPoint;
        if (isScheduled) {
            if (!flight.departure_lat || !flight.departure_lon) return;
            startPoint = [flight.departure_lat, flight.departure_lon];
        } else {
            if (currentPos?.latitude != null && currentPos?.longitude != null) {
                startPoint = [currentPos.latitude, currentPos.longitude];
            } else if (this.markers[aircraftId]) {
                const ll = this.markers[aircraftId].getLatLng();
                startPoint = [ll.lat, ll.lng];
            } else {
                return;
            }
        }

        const arr = [flight.arrival_lat, flight.arrival_lon];
        const label = isActive
            ? `En route to ${flight.arrival_iata || '?'}`
            : `Planned: ${flight.departure_iata || '?'} → ${flight.arrival_iata || '?'}`;

        const line = L.polyline([startPoint, arr], {
            color: isActive ? '#93c5fd' : '#a0a0a0',
            weight: isActive ? 1.5 : 2,
            opacity: isActive ? 0.45 : 0.6,
            dashArray: '8, 8'
        });

        line.bindTooltip(label, {
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
                    <div><span style="color:#556677">Alt:</span> ${Utils.formatAlt(data.altitude_ft)}${data.ground_elevation_ft != null && data.altitude_ft != null ? `<span style="color:#556677;font-size:10px"> (${Math.round(data.altitude_ft - data.ground_elevation_ft).toLocaleString()} AGL)</span>` : ''}</div>
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

        const aglFt = (pos.altitude_ft != null && pos.ground_elevation_ft != null)
            ? pos.altitude_ft - pos.ground_elevation_ft
            : null;
        const aglLine = aglFt != null
            ? `<div class="tt-sub" style="color:#8899aa">${Math.round(aglFt).toLocaleString()} ft AGL</div>`
            : '';

        const altHtml = fl
            ? `<div class="tt-value" style="color:${altColor}">${fl}</div><div class="tt-sub">${Utils.formatAlt(pos.altitude_ft)}</div>${aglLine}`
            : `<div class="tt-value" style="color:${altColor}">${Utils.formatAlt(pos.altitude_ft)}</div>${aglLine}`;

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
