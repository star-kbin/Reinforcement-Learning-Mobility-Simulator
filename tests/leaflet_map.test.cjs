const {test} = require('node:test');
const assert = require('node:assert/strict');
const {createMobilityMap} = require('../dashboard/leaflet_map/map.js');

function setup() {
  let maps = 0, tiles = 0, fits = 0, removed = 0, cleanups = 0;
  let viewReady = false;
  const camera = {center:[37.5,126.94],zoom:13};
  const map = {options:null, removeLayer(){removed++;}, invalidateSize(){}, remove(){},
    fitBounds(){fits++;viewReady=true;}, setView(){fits++;viewReady=true;}};
  function layer() {
    return {position:null, path:null, popup:null, tooltip:null,
      addTo(){return this;}, bindPopup(v){this.popup=v;return this;}, setPopupContent(v){this.popup=v;return this;},
      setLatLng(v){this.position=v;return this;}, setLatLngs(v){this.path=v;return this;},
      setStyle(){assert.ok(viewReady, 'Leaflet needs an initialized view before styling vector layers');return this;}, setIcon(v){this.icon=v;return this;}, bringToFront(){},
      getTooltip(){return this.tooltip;}, bindTooltip(v,options){this.tooltip={v,options};return this;}};
  }
  const L = {map(_,options){maps++;map.options=options;return map;},
    tileLayer(){tiles++;return layer();}, circleMarker:layer,marker:layer,polyline:layer,
    divIcon:v=>v, latLngBounds:v=>v};
  const renderer = createMobilityMap(L, {}, () => () => cleanups++);
  return {renderer, camera, map, counts:()=>({maps,tiles,fits,removed,cleanups})};
}
function payload(x=126.94) {
  return {view_bounds:[[37.4,126.9],[37.6,127]], vehicle_points:[{
    vehicle_id:'V1',label:'V1',coordinates:[x,37.51],color_hex:'#f00',status:'idle'}],
    request_origin_points:[{request_id:'<script>',coordinates:[126.95,37.52],color_hex:'#f90'}],
    request_destination_points:[{request_id:'R1',coordinates:[126.96,37.53],color_hex:'#00f'}],
    actual_vehicle_paths:[{vehicle_id:'V1',path:[[x,37.51],[126.95,37.52]],color_hex:'#f00'}]};
}

test('playback and seeking retain map, tiles, marker and path identities and user camera', () => {
  const s=setup(), r=s.renderer;
  r.update(payload(),'episode-1',false);
  const marker=r.layers.get('vehicle_points:V1'), path=r.layers.get('actual_vehicle_paths:V1:0');
  s.camera.center=[37.53,126.96]; s.camera.zoom=17.25;
  for (let i=0;i<100;i++) r.update(payload(126.94+i/10000),'episode-1',false);
  r.update(payload(),'episode-1',false); // Seek backwards
  assert.equal(r.layers.get('vehicle_points:V1'),marker);
  assert.equal(r.layers.get('actual_vehicle_paths:V1:0'),path);
  assert.deepEqual(marker.position,[37.51,126.94]);
  assert.deepEqual(s.counts(),{maps:1,tiles:1,fits:1,removed:0,cleanups:0});
  assert.deepEqual(s.camera,{center:[37.53,126.96],zoom:17.25});
  assert.equal(s.map.options.dragging,true); assert.equal(s.map.options.touchZoom,true);
  assert.equal(s.map.options.zoomControl,true);
  r.update(payload(),'episode-2',false);
  assert.equal(s.counts().fits,2);
});

test('removed entities disappear, requests have hover labels, vehicle labels are escaped and cleanup runs', () => {
  const s=setup(), p=payload();
  p.vehicle_points[0].label='<script>alert(1)</script>';
  s.renderer.update(p,'a',true);
  const origin=s.renderer.layers.get('request_origin_points:<script>');
  assert.equal(origin.tooltip.options.permanent,false);
  assert.ok(origin.tooltip.v.includes('&lt;script&gt;'));
  assert.ok(s.renderer.layers.get('vehicle_points:V1:label').icon.html.includes('&lt;script&gt;'));
  s.renderer.update({vehicle_points:[]},'a',true);
  assert.equal(s.renderer.layers.size,0);
  assert.ok(s.counts().removed>=5);
  s.renderer.destroy();
  assert.equal(s.counts().cleanups,1);
});
