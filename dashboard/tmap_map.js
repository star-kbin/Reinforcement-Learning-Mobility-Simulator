/* TMAP Raster Map JS V2: https://tmapapi.tmapmobility.com/main.html#webv2/sample/webSample01 */
(() => {
  "use strict";
  const { payload, appKey, compact } = JSON.parse(document.getElementById("tmap-data").textContent);
  const status = document.getElementById("status");
  const mapElement = document.getElementById("map");
  const fallback = document.getElementById("fallback");
  const toggle = document.getElementById("toggle");
  const retry = document.getElementById("retry");
  const fit = document.getElementById("fit");
  let map = null;
  let timer = null;
  let sdkScript = null;
  let attempt = 0;
  let failed = false;
  const markers = [];
  const lines = [];
  const points = ["vehicle_points", "vehicle_start_points", "request_origin_points", "request_destination_points"]
    .flatMap(name => payload[name] || []);
  const valid = coordinate => Array.isArray(coordinate) && coordinate.length >= 2 &&
    Number.isFinite(coordinate[0]) && Number.isFinite(coordinate[1]) &&
    Math.abs(coordinate[0]) <= 180 && Math.abs(coordinate[1]) <= 90;
  const coordinates = points.map(point => point.coordinates).filter(valid);
  // Payload는 [경도, 위도], TMAP LatLng는 (위도, 경도) 순서입니다.
  const latLng = coordinate => new window.Tmapv2.LatLng(coordinate[1], coordinate[0]);
  const escapeHTML = value => String(value ?? "").replace(/[&<>"']/g, char =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[char]));
  const color = value => /^#[0-9a-f]{6}$/i.test(value || "") ? value : "#0f766e";

  function tileReady(event) {
    // SDK 생성만으로 인증 성공이라 표시하지 않고 실제 지도 이미지 로드를 확인합니다.
    if (!map || failed || event.target.tagName !== "IMG" ||
        event.target.naturalWidth < 128 || event.target.naturalHeight < 128) return;
    clearTimeout(timer);
    status.textContent = "TMAP 지도 표시 중";
    retry.hidden = true;
  }
  mapElement.addEventListener("load", tileReady, true);

  function showCoordinates(visible) {
    mapElement.hidden = visible;
    fallback.hidden = !visible;
    toggle.textContent = visible ? "지도 보기" : "좌표 보기";
    if (!visible && map) {
      map.resize();
    }
  }

  function fail() {
    failed = true;
    clearTimeout(timer);
    status.textContent = "TMAP 지도를 불러오지 못했습니다";
    document.getElementById("notice").textContent =
      "App Key의 지도 서비스 권한·호출 한도와 네트워크를 확인하세요. 현재는 좌표 보기를 표시합니다.";
    retry.hidden = false;
    showCoordinates(true);
  }

  function fitBounds() {
    if (!map || !coordinates.length) return;
    if (coordinates.length === 1) {
      map.setCenter(latLng(coordinates[0]));
      map.setZoom(compact ? 14 : 15);
      return;
    }
    const bounds = new window.Tmapv2.LatLngBounds();
    coordinates.forEach(coordinate => bounds.extend(latLng(coordinate)));
    map.fitBounds(bounds);
  }

  function addMarker(point, isStart = false) {
    if (!valid(point.coordinates)) return;
    const fill = isStart ? "#ffffff" : color(point.fill_hex || point.color_hex);
    const border = color(point.border_hex || point.color_hex);
    const shape = point.marker_shape === "square" ? "5px" : "50%";
    const label = escapeHTML(point.label || point.vehicle_id || point.request_id);
    markers.push(new window.Tmapv2.Marker({
      position: latLng(point.coordinates), map,
      title: String(point.label_description || point.vehicle_id || point.request_id || ""),
      iconHTML: `<div style="width:34px;height:34px;border:3px solid ${border};border-radius:${shape};background:${fill};color:${isStart ? border : color(point.text_hex || "#ffffff")};display:flex;align-items:center;justify-content:center;font:bold 11px system-ui;box-shadow:0 1px 5px #0003">${label}</div>`,
      iconSize: new window.Tmapv2.Size(34, 34),
      offset: new window.Tmapv2.Point(17, 17),
    }));
  }

  function addLines(name, defaultColor, weight, opacity) {
    (payload[name] || []).forEach(line => {
      const path = (line.path || []).filter(valid);
      if (path.length < 2) return;
      lines.push(new window.Tmapv2.Polyline({
        path: path.map(latLng), map,
        strokeColor: color(line.color_hex || defaultColor),
        strokeWeight: weight, strokeOpacity: opacity,
      }));
    });
  }

  function initialize(currentAttempt) {
    if (currentAttempt !== attempt) return;
    try {
      if (!window.Tmapv2 || !coordinates.length) {
        fail();
        return;
      }
      map = new window.Tmapv2.Map("map", {
        center: latLng(coordinates[0]), width: "100%", height: "100%",
        zoom: compact ? 14 : 15, zoomControl: true, scrollwheel: true,
      });
      addLines("road_network_paths", "#94a3b8", 2, 0.45);
      addLines("request_pairs", "#cbd5e1", 1, 0.5);
      addLines("planned_route_history", "#94a3b8", 2, 0.5);
      addLines("actual_vehicle_paths", "#0f766e", 5, 0.9);
      addLines("active_routes", "#2563eb", 4, 0.8);
      (payload.vehicle_start_points || []).forEach(point => addMarker(point, true));
      (payload.request_origin_points || []).forEach(point => addMarker(point));
      (payload.request_destination_points || []).forEach(point => addMarker(point));
      (payload.vehicle_points || []).forEach(point => addMarker(point));
      fit.disabled = false;
      fitBounds();
      Array.from(mapElement.querySelectorAll("img")).filter(image => image.complete)
        .forEach(image => tileReady({target: image}));
    } catch (_) {
      // SDK 에러 문자열에 키나 요청 URL이 포함될 수 있어 화면에 그대로 노출하지 않습니다.
      fail();
    }
  }

  function start() {
    const currentAttempt = ++attempt;
    clearTimeout(timer);
    failed = false;
    markers.splice(0).forEach(marker => marker.setMap(null));
    lines.splice(0).forEach(line => line.setMap(null));
    if (map) { map.destroy(); map = null; }
    mapElement.replaceChildren();
    showCoordinates(false);
    status.textContent = "지도 연결 중…";
    document.getElementById("notice").textContent = "차량 · 요청 출발/도착 · 이동 경로를 표시합니다.";
    retry.hidden = true;
    fit.disabled = true;
    if (!appKey.trim()) { fail(); return; }
    timer = setTimeout(fail, 15000);
    if (window.Tmapv2) { initialize(currentAttempt); return; }
    if (sdkScript) sdkScript.remove();
    sdkScript = document.createElement("script");
    sdkScript.src = "https://apis.openapi.sk.com/tmap/jsv2?" + new URLSearchParams({ version: "1", appKey });
    sdkScript.onload = () => initialize(currentAttempt);
    sdkScript.onerror = () => { if (currentAttempt === attempt) fail(); };
    document.head.appendChild(sdkScript);
  }
  toggle.addEventListener("click", () => showCoordinates(fallback.hidden));
  retry.addEventListener("click", start);
  fit.addEventListener("click", fitBounds);
  window.addEventListener("resize", () => { if (map && !mapElement.hidden) map.resize(); });
  start();
})();
