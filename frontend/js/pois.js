/**
 * Planey - Points of Interest (POI) Manager
 * Right-click or long-press the map to place custom pins/landmarks.
 */

const POIs = {
    _pois: [],
    _markers: {},
    _visible: true,
    _editingId: null,
    _pendingLatLng: null,
    _selectedIcon: '📍',
    _selectedColor: '#00d4ff',
    _longPressTimer: null,
    _longPressStartPos: null,

    ICONS: [
        { key: '📍', label: 'Pin' },
        { key: '🏠', label: 'Home' },
        { key: '✈', label: 'Airport' },
        { key: '⛽', label: 'Fuel' },
        { key: '🚁', label: 'Heli' },
        { key: '⭐', label: 'Fav' },
        { key: '🏢', label: 'Facility' },
        { key: '🗺', label: 'Landmark' },
    ],

    COLORS: ['#00d4ff', '#00e676', '#ffab00', '#ff5252', '#b388ff', '#ffffff'],

    init() {
        this._load();

        // Right-click on map → open create modal
        FlightMap.map.on('contextmenu', (e) => {
            L.DomEvent.preventDefault(e.originalEvent);
            this._openCreateModal(e.latlng);
        });

        // Long press for mobile (600ms hold, cancel if finger moves >10px)
        const container = FlightMap.map.getContainer();

        container.addEventListener('touchstart', (e) => {
            if (e.touches.length !== 1) return;
            const t = e.touches[0];
            this._longPressStartPos = { x: t.clientX, y: t.clientY };
            this._longPressTimer = setTimeout(() => {
                const rect = container.getBoundingClientRect();
                const latlng = FlightMap.map.containerPointToLatLng(
                    L.point(this._longPressStartPos.x - rect.left, this._longPressStartPos.y - rect.top)
                );
                this._openCreateModal(latlng);
            }, 600);
        }, { passive: true });

        container.addEventListener('touchmove', (e) => {
            if (!this._longPressTimer || !this._longPressStartPos) return;
            const t = e.touches[0];
            const dx = t.clientX - this._longPressStartPos.x;
            const dy = t.clientY - this._longPressStartPos.y;
            if (Math.sqrt(dx * dx + dy * dy) > 10) {
                clearTimeout(this._longPressTimer);
                this._longPressTimer = null;
            }
        }, { passive: true });

        container.addEventListener('touchend', () => {
            clearTimeout(this._longPressTimer);
            this._longPressTimer = null;
        }, { passive: true });

        container.addEventListener('touchcancel', () => {
            clearTimeout(this._longPressTimer);
            this._longPressTimer = null;
        }, { passive: true });

        // Show/hide toggle button
        const toggleBtn = document.getElementById('btn-toggle-pois');
        if (toggleBtn) {
            toggleBtn.classList.toggle('active', this._visible);
            toggleBtn.addEventListener('click', () => this.toggleVisibility());
        }

        // Modal wiring
        document.getElementById('btn-close-poi').addEventListener('click', () => this._closeModal());
        document.getElementById('btn-cancel-poi').addEventListener('click', () => this._closeModal());
        document.getElementById('btn-confirm-poi').addEventListener('click', () => this._savePoi());
        document.getElementById('input-poi-name').addEventListener('keydown', (e) => {
            if (e.key === 'Enter') this._savePoi();
            if (e.key === 'Escape') this._closeModal();
        });

        // Close modal on overlay click
        document.getElementById('modal-poi').addEventListener('click', (e) => {
            if (e.target === document.getElementById('modal-poi')) this._closeModal();
        });

        this._renderAll();
    },

    _load() {
        try {
            const raw = localStorage.getItem('mapPois');
            this._pois = raw ? JSON.parse(raw) : [];
        } catch (_) {
            this._pois = [];
        }
        this._visible = localStorage.getItem('mapPoisVisible') !== '0';
    },

    _save() {
        localStorage.setItem('mapPois', JSON.stringify(this._pois));
    },

    _genId() {
        return `poi_${Date.now()}_${Math.random().toString(36).slice(2, 7)}`;
    },

    toggleVisibility() {
        this._visible = !this._visible;
        localStorage.setItem('mapPoisVisible', this._visible ? '1' : '0');
        document.getElementById('btn-toggle-pois')?.classList.toggle('active', this._visible);
        Object.values(this._markers).forEach(m => {
            if (this._visible) m.addTo(FlightMap.map);
            else m.remove();
        });
    },

    _renderAll() {
        Object.values(this._markers).forEach(m => m.remove());
        this._markers = {};
        this._pois.forEach(poi => this._addMarker(poi));
    },

    _buildIcon(poi) {
        const color = poi.color || '#00d4ff';
        return L.divIcon({
            className: '',
            html: `<div class="poi-marker-wrap">
                <div class="poi-marker-dot" style="background:${color};border-color:${color}">
                    <span class="poi-marker-emoji">${poi.icon || '📍'}</span>
                </div>
                <div class="poi-marker-tail" style="border-top-color:${color}"></div>
            </div>`,
            iconSize: [34, 44],
            iconAnchor: [17, 44],
            popupAnchor: [0, -46],
        });
    },

    _addMarker(poi) {
        const marker = L.marker([poi.lat, poi.lng], {
            icon: this._buildIcon(poi),
            zIndexOffset: 500,
        });

        marker.bindPopup(this._popupHtml(poi), {
            className: 'poi-popup-wrap',
            maxWidth: 220,
            minWidth: 160,
        });

        marker.on('popupopen', () => {
            const el = marker.getPopup().getElement();
            if (!el) return;
            el.querySelector('.btn-poi-edit')?.addEventListener('click', () => {
                marker.closePopup();
                this._openEditModal(poi.id);
            });
            el.querySelector('.btn-poi-delete')?.addEventListener('click', () => {
                marker.closePopup();
                this._deletePoi(poi.id);
            });
        });

        if (this._visible) marker.addTo(FlightMap.map);
        this._markers[poi.id] = marker;
    },

    _popupHtml(poi) {
        const notesHtml = poi.notes
            ? `<div class="poi-popup-notes">${poi.notes}</div>` : '';
        return `<div class="poi-popup">
            <div class="poi-popup-header">
                <span class="poi-popup-icon">${poi.icon || '📍'}</span>
                <span class="poi-popup-name">${poi.name || 'Unnamed Pin'}</span>
            </div>
            ${notesHtml}
            <div class="poi-popup-coords">${poi.lat.toFixed(5)}, ${poi.lng.toFixed(5)}</div>
            <div class="poi-popup-actions">
                <button class="btn-xs btn-poi-edit">Edit</button>
                <button class="btn-xs btn-poi-delete">Delete</button>
            </div>
        </div>`;
    },

    _removeMarker(id) {
        if (this._markers[id]) {
            this._markers[id].remove();
            delete this._markers[id];
        }
    },

    _openCreateModal(latlng) {
        this._editingId = null;
        this._pendingLatLng = latlng;
        this._selectedIcon = '📍';
        this._selectedColor = '#00d4ff';
        document.getElementById('modal-poi-title').textContent = 'Add Pin';
        document.getElementById('btn-confirm-poi').textContent = 'Add Pin';
        document.getElementById('input-poi-name').value = '';
        document.getElementById('input-poi-notes').value = '';
        document.getElementById('poi-coords-display').textContent =
            `${latlng.lat.toFixed(5)}, ${latlng.lng.toFixed(5)}`;
        this._renderIconGrid();
        this._renderColorSwatches();
        document.getElementById('modal-poi').style.display = '';
        setTimeout(() => document.getElementById('input-poi-name').focus(), 60);
    },

    _openEditModal(id) {
        const poi = this._pois.find(p => p.id === id);
        if (!poi) return;
        this._editingId = id;
        this._pendingLatLng = { lat: poi.lat, lng: poi.lng };
        this._selectedIcon = poi.icon || '📍';
        this._selectedColor = poi.color || '#00d4ff';
        document.getElementById('modal-poi-title').textContent = 'Edit Pin';
        document.getElementById('btn-confirm-poi').textContent = 'Save Pin';
        document.getElementById('input-poi-name').value = poi.name || '';
        document.getElementById('input-poi-notes').value = poi.notes || '';
        document.getElementById('poi-coords-display').textContent =
            `${poi.lat.toFixed(5)}, ${poi.lng.toFixed(5)}`;
        this._renderIconGrid();
        this._renderColorSwatches();
        document.getElementById('modal-poi').style.display = '';
        setTimeout(() => document.getElementById('input-poi-name').focus(), 60);
    },

    _closeModal() {
        document.getElementById('modal-poi').style.display = 'none';
        this._editingId = null;
        this._pendingLatLng = null;
    },

    _renderIconGrid() {
        const grid = document.getElementById('poi-icon-grid');
        grid.innerHTML = this.ICONS.map(ic => `
            <div class="poi-icon-cell${this._selectedIcon === ic.key ? ' selected' : ''}" data-icon="${ic.key}">
                <span class="poi-icon-emoji">${ic.key}</span>
                <span class="poi-icon-label">${ic.label}</span>
            </div>
        `).join('');
        grid.querySelectorAll('.poi-icon-cell').forEach(cell => {
            cell.addEventListener('click', () => {
                this._selectedIcon = cell.dataset.icon;
                grid.querySelectorAll('.poi-icon-cell').forEach(c => c.classList.remove('selected'));
                cell.classList.add('selected');
            });
        });
    },

    _renderColorSwatches() {
        const wrap = document.getElementById('poi-color-swatches');
        wrap.innerHTML = this.COLORS.map(c => `
            <div class="poi-color-swatch${this._selectedColor === c ? ' selected' : ''}"
                 data-color="${c}" style="background:${c}" title="${c}"></div>
        `).join('');
        wrap.querySelectorAll('.poi-color-swatch').forEach(sw => {
            sw.addEventListener('click', () => {
                this._selectedColor = sw.dataset.color;
                wrap.querySelectorAll('.poi-color-swatch').forEach(s => s.classList.remove('selected'));
                sw.classList.add('selected');
            });
        });
    },

    _savePoi() {
        const name = document.getElementById('input-poi-name').value.trim();
        if (!name) {
            Utils.toast('Enter a name for this pin', 'warning');
            document.getElementById('input-poi-name').focus();
            return;
        }
        const latlng = this._pendingLatLng;
        if (!latlng) return;

        const notes = document.getElementById('input-poi-notes').value.trim();

        if (this._editingId) {
            const idx = this._pois.findIndex(p => p.id === this._editingId);
            if (idx === -1) return;
            this._pois[idx] = { ...this._pois[idx], name, icon: this._selectedIcon, color: this._selectedColor, notes };
            this._removeMarker(this._editingId);
            this._addMarker(this._pois[idx]);
            Utils.toast('Pin updated', 'success');
        } else {
            const poi = {
                id: this._genId(),
                name,
                icon: this._selectedIcon,
                color: this._selectedColor,
                lat: latlng.lat,
                lng: latlng.lng,
                notes,
            };
            this._pois.push(poi);
            this._addMarker(poi);
            Utils.toast('Pin added', 'success');
        }

        this._save();
        this._closeModal();
    },

    _deletePoi(id) {
        if (!confirm('Remove this pin?')) return;
        this._pois = this._pois.filter(p => p.id !== id);
        this._removeMarker(id);
        this._save();
        Utils.toast('Pin removed', 'success');
    },
};
