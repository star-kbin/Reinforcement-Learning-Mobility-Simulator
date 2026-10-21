/* One map and tile layer per iframe. Frame updates reconcile keyed data layers. */
function createMobilityMap(L, container, attachWheel) {
  const map = L.map(container, {
    zoomControl:true, scrollWheelZoom:false, zoomSnap:0.25, zoomDelta:0.25,
    dragging:true, touchZoom:true, doubleClickZoom:false, boxZoom:true, keyboard:true,
    zoomAnimation:false, fadeAnimation:false, markerZoomAnimation:false
  });
  const tiles = L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom:19, attribution:'&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap contributors</a>'
  }).addTo(map);
  const cleanupWheel = attachWheel(map, L);
  const layers = new Map();
  let viewId;
  let initialized = false;
  const escapeHtml = value => String(value ?? '').replace(/[&<>"']/g, c =>
    ({'&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;'}[c]));
  const latlng = p => [p.coordinates[1], p.coordinates[0]];

  function update(payload, nextViewId, compact) {
    // Reframe only for a new episode. Neither a tick nor seeking resets the camera.
    if (!initialized || nextViewId !== viewId) {
      const points = ['vehicle_points','vehicle_start_points','request_origin_points','request_destination_points']
        .flatMap(kind => (payload[kind] || []).map(latlng));
      if (payload.view_bounds) map.fitBounds(payload.view_bounds, {padding:[40,40],maxZoom:16,animate:false});
      else if (points.length > 1) map.fitBounds(L.latLngBounds(points), {padding:[24,24],maxZoom:16,animate:false});
      else map.setView(points[0] || [37.513620,126.941570],13,{animate:false});
      initialized = true;
      viewId = nextViewId;
    }
    const present = new Set();
    function reconcile(key, create, refresh) {
      present.add(key);
      let layer = layers.get(key);
      if (!layer) { layer = create().addTo(map); layers.set(key, layer); }
      refresh(layer);
      return layer;
    }
    const pathKinds = [
      ['road_network_paths',6,0.9,null], ['planned_route_history',3,0.5,'4 8'],
      ['actual_vehicle_paths',4,0.95,null], ['active_routes',5,0.95,'8 6']
    ];
    for (const [kind, weight, opacity, dashArray] of pathKinds) {
      (payload[kind] || []).forEach((line, i) => {
        const points = (line.path || []).map(p => [p[1],p[0]]);
        if (points.length < 2) return;
        reconcile(`${kind}:${line.vehicle_id}:${i}`, () => L.polyline(points), layer => {
          layer.setLatLngs(points).setStyle({color:line.color_hex, weight, opacity, dashArray});
        });
      });
    }
    for (const kind of ['vehicle_start_points','request_origin_points','request_destination_points','vehicle_points']) {
      (payload[kind] || []).forEach((point, i) => {
        const vehicle = kind === 'vehicle_points';
        const start = kind === 'vehicle_start_points';
        const role = kind === 'request_destination_points' ? '목적지' : '출발지';
        const key = `${kind}:${point.vehicle_id || point.request_id || i}`;
        const popup = vehicle ?
          `${escapeHtml(point.label)} · ${escapeHtml(point.status)}<br>탑승 / 용량: ${escapeHtml(point.current_load)} / ${escapeHtml(point.capacity)}<br>배정 요청: ${escapeHtml(point.assigned_request_id || '-')}<br>완료 요청: ${escapeHtml(point.completed_request_count)}<br>누적 실주행: ${escapeHtml(point.actual_distance_km)} km<br>현재 위치: ${escapeHtml(point.position_text)}` :
          escapeHtml(start ? `차량 시작 위치 · ${point.vehicle_id}` : `${role} · ${point.request_id || point.label}`);
        const style = {
          radius:vehicle ? (compact ? 8 : 11) : 7,
          color:point.border_hex || point.color_hex || '#ffffff',
          weight:point.border_width || 3,
          fillColor:start ? '#ffffff' : (point.fill_hex || point.color_hex), fillOpacity:0.95
        };
        // Destination squares remain distinct from origin circles, with hover labels.
        const square = kind === 'request_destination_points';
        const squareIcon = () => L.divIcon({className:'', iconSize:[14,14], iconAnchor:[7,7],
          html:`<div style="width:12px;height:12px;border:2px solid white;background:${escapeHtml(point.fill_hex || point.color_hex)}"></div>`});
        const marker = reconcile(key,
          () => (square ? L.marker(latlng(point), {icon:squareIcon()}) : L.circleMarker(latlng(point), style)).bindPopup(popup),
          layer => {
            layer.setLatLng(latlng(point));
            if (square) layer.setIcon(squareIcon()); else layer.setStyle(style);
            layer.setPopupContent(popup);
          });
        if (!vehicle && !start && !marker.getTooltip()) {
          marker.bindTooltip(popup, {permanent:false, direction:'top'});
        }
        if (vehicle) {
          marker.bringToFront();
          const offset = point.label_offset || [0,-10];
          const icon = L.divIcon({className:'', iconAnchor:[0,0],
            html:`<span class="vehicle-label" style="display:inline-block;transform:translate(${Number(offset[0])}px,${Number(offset[1])}px);background:${escapeHtml(point.color_hex)};border-color:${escapeHtml(point.border_hex || '#fff')}">${escapeHtml(point.label)}</span>`});
          reconcile(key+':label', () => L.marker(latlng(point), {interactive:false, keyboard:false, zIndexOffset:900, icon}),
            layer => layer.setLatLng(latlng(point)).setIcon(icon));
        }
      });
    }
    for (const [key, layer] of layers) {
      if (!present.has(key)) { map.removeLayer(layer); layers.delete(key); }
    }
    map.invalidateSize({pan:false});
  }
  function destroy() { cleanupWheel(); map.remove(); layers.clear(); }
  return {map, tiles, layers, update, destroy};
}
if (typeof module !== 'undefined' && module.exports) module.exports = {createMobilityMap};
