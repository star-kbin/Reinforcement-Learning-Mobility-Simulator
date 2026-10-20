/* Bounded, center-anchored wheel zoom for Leaflet. Native wheel zoom stays off. */
function attachStableWheelZoom(map, leaflet) {
  const container = map.getContainer();
  const step = 0.25;
  const intervalMs = 160;
  let timer = null;
  let pendingDirection = 0;

  function cancelPending() {
    if (timer !== null) clearTimeout(timer);
    timer = null;
    pendingDirection = 0;
  }

  function applyStep() {
    timer = null;
    const direction = pendingDirection;
    pendingDirection = 0;
    const current = map.getZoom();
    const target = Math.max(map.getMinZoom(), Math.min(map.getMaxZoom(), current + direction * step));
    if (target !== current) {
      // setZoom preserves the center: the pointer cannot pull the camera sideways.
      map.setZoom(target, { animate: false });
    }
  }

  function onWheel(event) {
    // Leave browser Ctrl/Command zoom and trackpad pinch gestures to the browser.
    if (event.ctrlKey || event.metaKey) {
      cancelPending();
      return;
    }
    const delta = leaflet.DomEvent.getWheelDelta(event);
    if (!Number.isFinite(delta) || delta === 0) return;
    event.preventDefault();
    event.stopPropagation();
    // Keep only the latest direction, never a backlog of wheel momentum.
    pendingDirection = Math.sign(delta);
    if (timer === null) timer = setTimeout(applyStep, intervalMs);
  }

  function cleanup() {
    cancelPending();
    container.removeEventListener("wheel", onWheel);
    container.removeEventListener("mouseleave", cancelPending);
    map.off("unload", cleanup);
  }

  container.addEventListener("wheel", onWheel, { passive: false });
  container.addEventListener("mouseleave", cancelPending);
  map.on("unload", cleanup);
  return cleanup;
}

if (typeof module !== "undefined" && module.exports) {
  module.exports = { attachStableWheelZoom };
}
