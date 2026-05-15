/**
 * Planey - Timeline Component
 * Interactive timeline bar for scrubbing through flight position history
 */

const Timeline = {
    positions: [],
    currentIndex: 0,
    isPlaying: false,
    playInterval: null,
    flightId: null,
    aircraftId: null,
    isDragging: false,

    init() {
        const track = document.getElementById('timeline-track');
        const scrubber = document.getElementById('timeline-scrubber');

        // Click on track to seek
        track.addEventListener('click', (e) => {
            if (this.positions.length === 0) return;
            const rect = track.getBoundingClientRect();
            const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            this.seekTo(pct);
        });

        // Drag scrubber
        scrubber.addEventListener('mousedown', (e) => { this.isDragging = true; e.preventDefault(); });
        scrubber.addEventListener('touchstart', (e) => { this.isDragging = true; }, { passive: true });

        document.addEventListener('mousemove', (e) => {
            if (!this.isDragging) return;
            const rect = track.getBoundingClientRect();
            const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
            this.seekTo(pct);
        });
        document.addEventListener('touchmove', (e) => {
            if (!this.isDragging) return;
            const rect = track.getBoundingClientRect();
            const touch = e.touches[0];
            const pct = Math.max(0, Math.min(1, (touch.clientX - rect.left) / rect.width));
            this.seekTo(pct);
        });
        document.addEventListener('mouseup', () => { this.isDragging = false; });
        document.addEventListener('touchend', () => { this.isDragging = false; });

        // Close button
        document.getElementById('btn-close-timeline').addEventListener('click', () => this.hide());
    },

    /**
     * Load positions for a flight and show the timeline
     */
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
            this.currentIndex = 0;

            document.getElementById('timeline-title').textContent = title || 'Flight Timeline';
            document.getElementById('timeline-bar').style.display = 'block';

            // Draw the full trail on the map
            FlightMap.drawTrail(aircraftId, positions);

            // Show first position
            this._updateDisplay();

            // Fit map to trail
            if (positions.length > 1) {
                const lats = positions.map(p => p.latitude);
                const lngs = positions.map(p => p.longitude);
                FlightMap.map.fitBounds([
                    [Math.min(...lats), Math.min(...lngs)],
                    [Math.max(...lats), Math.max(...lngs)]
                ], { padding: [50, 50] });
            }
        } catch (err) {
            Utils.toast('Failed to load flight positions', 'error');
            console.error(err);
        }
    },

    /**
     * Load positions by aircraft ID and time range
     */
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
            this.currentIndex = 0;

            document.getElementById('timeline-title').textContent = `${tailNumber} — Last ${hours}h`;
            document.getElementById('timeline-bar').style.display = 'block';

            FlightMap.drawTrail(aircraftId, positions);
            this._updateDisplay();

            if (positions.length > 1) {
                const lats = positions.map(p => p.latitude);
                const lngs = positions.map(p => p.longitude);
                FlightMap.map.fitBounds([
                    [Math.min(...lats), Math.min(...lngs)],
                    [Math.max(...lats), Math.max(...lngs)]
                ], { padding: [50, 50] });
            }
        } catch (err) {
            Utils.toast('Failed to load position history', 'error');
            console.error(err);
        }
    },

    seekTo(pct) {
        if (this.positions.length === 0) return;
        this.currentIndex = Math.round(pct * (this.positions.length - 1));
        this._updateDisplay();
    },

    hide() {
        document.getElementById('timeline-bar').style.display = 'none';
        this.stopPlay();
        
        // Save aircraft ID before clearing state so we can restore live view
        const acId = this.aircraftId;
        
        this.positions = [];
        this.flightId = null;
        this.aircraftId = null;

        // Automatically snap back to live real-time position
        if (acId && window.Flights) {
            // First clear the trail and any history marker
            if (window.FlightMap) {
                FlightMap.clearTrail(acId);
            }
            
            // Re-poll live location and refresh UI silently
            window.API.pollAircraft(acId).then(pos => {
                if (pos) {
                    console.log("Restored live position after closing history");
                    Flights.loadAircraft();
                }
            }).catch(e => console.error("Failed to restore live position", e));
        }
    },

    startPlay() {
        if (this.isPlaying) return;
        this.isPlaying = true;
        this.playInterval = setInterval(() => {
            if (this.currentIndex >= this.positions.length - 1) {
                this.stopPlay();
                return;
            }
            this.currentIndex++;
            this._updateDisplay();
        }, 200);
    },

    stopPlay() {
        this.isPlaying = false;
        if (this.playInterval) {
            clearInterval(this.playInterval);
            this.playInterval = null;
        }
    },

    _updateDisplay() {
        if (this.positions.length === 0) return;

        const pos = this.positions[this.currentIndex];
        const pct = this.positions.length > 1
            ? (this.currentIndex / (this.positions.length - 1)) * 100
            : 0;

        // Update progress bar and scrubber
        document.getElementById('timeline-progress').style.width = `${pct}%`;
        document.getElementById('timeline-scrubber').style.left = `${pct}%`;

        // Update info
        document.getElementById('timeline-time').textContent = Utils.formatTime(pos.timestamp);
        document.getElementById('timeline-alt').textContent = Utils.formatAlt(pos.altitude_ft);
        document.getElementById('timeline-speed').textContent = Utils.formatSpeed(pos.ground_speed_kts);

        // Move map marker to this position
        if (this.aircraftId && FlightMap.markers[this.aircraftId]) {
            FlightMap.markers[this.aircraftId].setLatLng([pos.latitude, pos.longitude]);
            const color = Utils.altitudeColor(pos.altitude_ft);
            FlightMap.markers[this.aircraftId].setIcon(Utils.airplaneIcon(pos.heading, color));
        }
    }
};
