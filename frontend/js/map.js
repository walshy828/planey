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
    _currentLayerName: 'dark',

    _layerDefs: {
        dark: {
            url: 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png',
            opts: { maxZoom: 19, subdomains: 'abcd' },
            attribution: '© <a href="https://carto.com/">CARTO</a> · <a href="https://opensky-network.org">OpenSky</a>'
        },
        light: {
            url: 'https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png',
            opts: { maxZoom: 19, subdomains: 'abcd' },
            attribution: '© <a href="https://carto.com/">CARTO</a> · <a href="https://opensky-network.org">OpenSky</a>'
        },
        satellite: {
            url: 'https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}',
            opts: { maxZoom: 19 },
            attribution: '© <a href="https://www.esri.com/">Esri</a> · <a href="https://opensky-network.org">OpenSky</a>'
        }
    },

    init() {
        this.map = L.map('map', {
            center: [39.8283, -98.5795], // Center US
            zoom: 4,
            zoomControl: true,
            attributionControl: false
        });

        // Build tile layer instances
        this._tileLayers = {};
        for (const [name, def] of Object.entries(this._layerDefs)) {
            this._tileLayers[name] = L.tileLayer(def.url, def.opts);
        }
        this._tileLayers.dark.addTo(this.map);

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
    },

    setLayer(name) {
        if (!this._tileLayers[name]) return;
        this._tileLayers[this._currentLayerName].remove();
        this._currentLayerName = name;
        this._tileLayers[name].addTo(this.map);

        // Satellite view: lighten trail colors so they read against imagery
        const isSatellite = name === 'satellite';
        Object.values(this.trails).forEach(segs =>
            segs.forEach(seg => seg.setStyle({ weight: isSatellite ? 3 : 4, opacity: isSatellite ? 0.95 : 0.8 }))
        );
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
                className: 'flight-tooltip',
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
            this._addTrailPoint(aircraftId, pos, altitude_ft);
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
    _addTrailPoint(aircraftId, pos, altFt) {
        if (!this.trails[aircraftId]) this.trails[aircraftId] = [];
        const segments = this.trails[aircraftId];

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
            
            seg.bindTooltip(this._trailTooltipHtml({
                altitude_ft: altFt,
                timestamp: new Date().toISOString()
            }), {
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

    _trailTooltipHtml(pos) {
        return `
            <div style="font-size:11px; line-height:1.2">
                <div style="color:#00d4ff; font-weight:bold">${Utils.formatDateTime(pos.timestamp)}</div>
                <div>Alt: ${Utils.formatAlt(pos.altitude_ft)}</div>
                ${pos.ground_speed_kts ? `<div>Spd: ${Utils.formatSpeed(pos.ground_speed_kts)}</div>` : ''}
            </div>
        `;
    },

    _tooltipHtml(data) {
        return `
            <div style="line-height:1.2">
                <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:2px">
                    <span style="font-weight:bold;color:#00d4ff;">${data.tail_number}${data.category === 'helicopter' ? ' 🚁' : ''}</span>
                    ${data.timestamp ? `<span class="live-time-ago" data-timestamp="${data.timestamp}" style="font-size:9px;color:#8899aa;margin-left:8px;">${Utils.timeAgo(data.timestamp)}</span>` : ''}
                </div>
                <div style="font-size:11px">
                    ${Utils.formatAlt(data.altitude_ft)} | ${Utils.formatSpeed(data.ground_speed_kts)}<br/>
                    ${Utils.formatHeading(data.heading)} | ${Utils.formatVRate(data.vertical_rate_fpm)}
                </div>
            </div>
        `;
    }
};
