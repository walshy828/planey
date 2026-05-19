/**
 * Planey - Utility Functions
 * Formatting, unit conversions, color interpolation
 */

const Utils = {
    /**
     * Get altitude color based on feet (Aviation standard scale)
     * Orange(0) → Yellow(4K) → Green(8K) → Cyan(10K-20K) → Blue(30K) → Purple(40K+)
     */
    altitudeColor(altFt) {
        if (altFt == null) return '#888888';
        const a = Math.max(0, altFt);
        if (a <= 2000) return this._lerp('#ff8000', '#ffb300', a / 2000);
        if (a <= 4000) return this._lerp('#ffb300', '#ffea00', (a - 2000) / 2000);
        if (a <= 6000) return this._lerp('#ffea00', '#a2ff00', (a - 4000) / 2000);
        if (a <= 8000) return this._lerp('#a2ff00', '#00ff00', (a - 6000) / 2000);
        if (a <= 10000) return this._lerp('#00ff00', '#00ffb3', (a - 8000) / 2000);
        if (a <= 20000) return this._lerp('#00ffb3', '#00aaff', (a - 10000) / 10000);
        if (a <= 30000) return this._lerp('#00aaff', '#0000ff', (a - 20000) / 10000);
        if (a <= 40000) return this._lerp('#0000ff', '#b300ff', (a - 30000) / 10000);
        return '#b300ff';
    },

    _lerp(c1, c2, t) {
        const r1 = parseInt(c1.slice(1, 3), 16), g1 = parseInt(c1.slice(3, 5), 16), b1 = parseInt(c1.slice(5, 7), 16);
        const r2 = parseInt(c2.slice(1, 3), 16), g2 = parseInt(c2.slice(3, 5), 16), b2 = parseInt(c2.slice(5, 7), 16);
        const r = Math.round(r1 + (r2 - r1) * t), g = Math.round(g1 + (g2 - g1) * t), b = Math.round(b1 + (b2 - b1) * t);
        return `#${r.toString(16).padStart(2,'0')}${g.toString(16).padStart(2,'0')}${b.toString(16).padStart(2,'0')}`;
    },

    formatAlt(ft) { return ft != null ? `${Math.round(ft).toLocaleString()} ft` : '—'; },
    formatSpeed(kts) { return kts != null ? `${Math.round(kts)} kts` : '—'; },
    formatHeading(deg) { return deg != null ? `${Math.round(deg)}°` : '—'; },
    formatVRate(fpm) {
        if (fpm == null) return '—';
        const v = Math.round(fpm);
        return v > 0 ? `↑ ${v} fpm` : v < 0 ? `↓ ${Math.abs(v)} fpm` : '0 fpm';
    },

    formatTime(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },

    formatDateTime(iso) {
        if (!iso) return '—';
        const d = new Date(iso);
        return d.toLocaleDateString([], { month: 'short', day: 'numeric' }) + ' ' + d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    },

    timeAgo(iso) {
        if (!iso) return '—';
        const s = Math.floor((Date.now() - new Date(iso).getTime()) / 1000);
        if (s < 60) return `${s}s ago`;
        if (s < 3600) return `${Math.floor(s / 60)}m ago`;
        if (s < 86400) return `${Math.floor(s / 3600)}h ago`;
        return `${Math.floor(s / 86400)}d ago`;
    },

    statusBadge(status) {
        const cls = {
            active: 'badge-active', scheduled: 'badge-scheduled', ground: 'badge-ground',
            landed: 'badge-landed', cancelled: 'badge-cancelled', unknown: 'badge-ground'
        };
        return `<span class="badge ${cls[status] || 'badge-ground'}">${status}</span>`;
    },

    /** Show toast notification */
    toast(msg, type = 'info', duration = 4000) {
        const c = document.getElementById('toast-container');
        const t = document.createElement('div');
        t.className = `toast ${type}`;
        t.textContent = msg;
        c.appendChild(t);
        setTimeout(() => { t.style.opacity = '0'; t.style.transform = 'translateX(100px)'; t.style.transition = '0.3s ease'; setTimeout(() => t.remove(), 300); }, duration);
    },

    /** Reverse geocode a location */
    async getLocationName(lat, lon) {
        if (lat == null || lon == null) return 'Unknown';
        
        // Cache to avoid API limits (1 req/sec max for Nominatim)
        // We round to 2 decimal places to cache nearby points (~1km accuracy)
        const key = `${lat.toFixed(2)},${lon.toFixed(2)}`;
        this._geocodeCache = this._geocodeCache || {};
        
        if (this._geocodeCache[key]) {
            return this._geocodeCache[key];
        }

        try {
            const res = await fetch(`https://nominatim.openstreetmap.org/reverse?format=json&lat=${lat}&lon=${lon}&zoom=10`, {
                headers: { 'User-Agent': 'Planey Flight Tracker' }
            });
            if (!res.ok) return 'Unknown';
            const data = await res.json();
            
            let name = 'Unknown';
            if (data && data.address) {
                const city = data.address.city || data.address.town || data.address.village || data.address.county;
                const state = data.address.state || data.address.country;
                if (city && state) name = `${city}, ${state}`;
                else if (city) name = city;
                else if (state) name = state;
            }
            
            this._geocodeCache[key] = name;
            return name;
        } catch (e) {
            console.error('Geocoding failed:', e);
            return 'Unknown';
        }
    },

    /** Create airplane SVG icon for map markers */
    airplaneIcon(heading = 0, color = '#00d4ff') {
        const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24">
            <g style="transform: rotate(${heading || 0}deg); transform-origin: center;">
                <path d="M21,16L21,14L13,9L13,3.5A1.5,1.5 0 0,0 11.5,2A1.5,1.5 0 0,0 10,3.5L10,9L2,14L2,16L10,13.5L10,19L8,20.5L8,22L11.5,21L15,22L15,20.5L13,19L13,13.5L21,16Z" 
                      fill="${color}" stroke="#000" stroke-width="1" stroke-linejoin="round"/>
            </g>
        </svg>`;
        return L.divIcon({
            html: svg,
            iconSize: [32, 32],
            iconAnchor: [16, 16],
            className: 'airplane-marker'
        });
    },

    /** Create aircraft (airplane or helicopter) SVG icon for map markers */
    aircraftIcon(heading = 0, color = '#00d4ff', category = 'plane') {
        if (category === 'helicopter') {
            const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 24 24">
                <g style="transform: rotate(${heading || 0}deg); transform-origin: center;">
                    <!-- Skids -->
                    <path d="M7 8v9M17 8v9M7 11h3M14 11h3M7 14h3M14 14h3" stroke="${color}" stroke-width="1.5" stroke-linecap="round"/>
                    <path d="M7 8v9M17 8v9" stroke="#000" stroke-width="0.5" stroke-linecap="round"/>
                    <!-- Tail boom & rotor -->
                    <path d="M12 15v6M9 21h6" stroke="${color}" stroke-width="2" stroke-linecap="round"/>
                    <path d="M12 15v6M9 21h6" stroke="#000" stroke-width="0.5" stroke-linecap="round"/>
                    <!-- Fuselage -->
                    <rect x="9" y="7" width="6" height="9" rx="3" fill="${color}" stroke="#000" stroke-width="1" stroke-linejoin="round"/>
                    <!-- Rotor Blades -->
                    <path d="M2 11.5h20M12 1.5v20" stroke="${color}" stroke-width="1.2" stroke-linecap="round" opacity="0.8"/>
                    <!-- Rotor Hub -->
                    <circle cx="12" cy="11.5" r="1.5" fill="#ffffff" stroke="#000" stroke-width="0.5"/>
                </g>
            </svg>`;
            return L.divIcon({
                html: svg,
                iconSize: [32, 32],
                iconAnchor: [16, 16],
                className: 'airplane-marker'
            });
        }
        return this.airplaneIcon(heading, color);
    }
};
