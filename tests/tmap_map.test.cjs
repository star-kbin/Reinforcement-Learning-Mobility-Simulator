// 실제 키나 네트워크 없이 TMAP SDK 경계·실패·재시도를 확인합니다.
const {test} = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../dashboard/tmap_map.js'), 'utf8');

function setup({sdk = true, key = 'test&key=한글', mapFails = false} = {}) {
  const elements = {};
  const timers = new Set();
  const delays = new WeakMap();
  const scripts = [];
  const maps = [], markers = [], lines = [];
  for (const id of ['status', 'map', 'fallback', 'toggle', 'retry', 'fit', 'notice', 'tmap-data']) {
    elements[id] = {hidden: ['fallback','retry'].includes(id), listeners: {},
      addEventListener(name, callback) { this.listeners[name] = callback; },
      replaceChildren() {}, querySelectorAll() { return []; }, textContent: '', disabled: false};
  }
  elements['tmap-data'].textContent = JSON.stringify({appKey:key, compact:true, payload:{
    vehicle_points:[{coordinates:[127.03,37.49],label:'<script>bad</script>',color_hex:'#0f766e'}],
    request_origin_points:[{coordinates:[127.04,37.5],label:'O1'}],
    active_routes:[{path:[[127.03,37.49],[127.04,37.5]],color_hex:'#2563eb'}]
  }});
  class Map {
    constructor(id, options) {
      if (mapFails) throw new Error('test');
      this.options=options; maps.push(this);
    }
    fitBounds(bounds) { this.bounds=bounds; }
    resize() {}
    destroy() { this.destroyed=true; }
  }
  class LatLng { constructor(lat,lon) { this.lat=lat; this.lon=lon; } }
  class LatLngBounds { constructor() { this.points=[]; } extend(p) { this.points.push(p); } }
  class Marker { constructor(options) { markers.push(options); } setMap() {} }
  class Polyline { constructor(options) { lines.push(options); } setMap() {} }
  let reloaded = false;
  const window = {addEventListener() {}, location:{reload(){reloaded=true;}}};
  const sdkObject = {Map, LatLng, LatLngBounds, Marker, Polyline, Size:class{}, Point:class{}};
  if (sdk) window.Tmapv2 = sdkObject;
  const document = {getElementById:id=>elements[id],
    createElement() { return {remove() { this.removed=true; }}; },
    head:{appendChild(script) { scripts.push(script); }}};
  vm.runInNewContext(source, {window, document, URLSearchParams,
    setTimeout(callback, delay) { timers.add(callback); delays.set(callback,delay); return callback; },
    clearTimeout(callback) { timers.delete(callback); }});
  function runTimer(delay) {
    const callback=[...timers].find(callback=>delays.get(callback)===delay);
    assert.ok(callback,`pending timer ${delay}`);
    timers.delete(callback);
    callback();
  }
  return {elements, maps, markers, lines, timers, scripts, window, runTimer,
    installSdk:()=>{window.Tmapv2=sdkObject;}, wasReloaded:()=>reloaded};
}

test('좌표 순서·차량/요청 마커·경로·범위', () => {
  const {maps,markers,lines} = setup();
  assert.equal(maps[0].options.center.lat,37.49);
  assert.equal(maps[0].options.center.lon,127.03);
  assert.equal(maps[0].options.height,'340px');
  assert.equal(markers.length,2);
  assert.equal(lines.length,1);
  assert.equal(maps[0].bounds.points.length,2);
  assert.ok(markers[1].iconHTML.includes('&lt;script&gt;'));
  assert.ok(!markers[1].iconHTML.includes('<script>'));
});
test('SDK 생성만으로 성공을 표시하지 않고 실제 타일 로드 확인', () => {
  const {elements,timers} = setup();
  assert.equal(elements.status.textContent,'지도 연결 중…');
  elements.map.listeners.load({target:{tagName:'IMG',naturalWidth:80,naturalHeight:30}});
  assert.equal(elements.status.textContent,'지도 연결 중…');
  elements.map.listeners.load({target:{tagName:'IMG',naturalWidth:256,naturalHeight:256}});
  assert.equal(elements.status.textContent,'TMAP 지도 표시 중');
  assert.equal(timers.size,0);
});
test('시간 초과 시 좌표 보기로 전환', () => {
  const {elements,timers} = setup();
  [...timers][0]();
  assert.equal(elements.fallback.hidden,false);
  assert.equal(elements.retry.hidden,false);
  assert.ok(elements.notice.textContent.includes('권한'));
});
test('SDK 로드 실패 진단과 문서 재로딩', () => {
  const {elements,scripts,wasReloaded,runTimer,timers} = setup({sdk:false});
  assert.equal(elements.fallback.hidden,true);
  runTimer(15000);
  assert.equal(elements.fallback.hidden,false);
  assert.equal(elements.status.textContent,'TMAP SDK 준비 대기 시간이 초과되었습니다');
  assert.equal(timers.size,0);
  assert.ok(elements.notice.textContent.includes('SDK 로드 실패'));
  assert.ok(elements.notice.textContent.includes('IP'));
  elements.retry.listeners.click();
  assert.equal(wasReloaded(),true);
  assert.equal(scripts.length,0);
});
test('지도 생성 오류와 인증 오류를 구분', () => {
  const {elements} = setup({mapFails:true});
  assert.ok(elements.notice.textContent.includes('지도 생성 실패'));
  assert.ok(!elements.notice.textContent.includes('test&key'));
});
test('하위 SDK가 늦게 준비되면 기다린 뒤 지도 생성', () => {
  const {elements,maps,installSdk,runTimer,timers} = setup({sdk:false});
  assert.equal(elements.status.textContent,'TMAP SDK 준비 중…');
  assert.equal(maps.length,0);
  installSdk();
  runTimer(100);
  assert.equal(maps.length,1);
  elements.map.listeners.load({target:{tagName:'IMG',naturalWidth:256,naturalHeight:256}});
  assert.equal(elements.status.textContent,'TMAP 지도 표시 중');
  assert.equal(timers.size,0);
});
test('네임스페이스만 먼저 생긴 경우에도 Map 생성자를 기다림', () => {
  const {window,maps,installSdk,runTimer} = setup({sdk:false});
  window.Tmapv2={};
  runTimer(100);
  assert.equal(maps.length,0);
  installSdk();
  runTimer(100);
  assert.equal(maps.length,1);
});
test('키가 없을 때 외부 SDK를 호출하지 않음', () => {
  const {elements,scripts} = setup({key:'',sdk:false});
  assert.equal(scripts.length,0);
  assert.equal(elements.fallback.hidden,false);
});
test('좌표 보기와 지도 보기 전환', () => {
  const {elements} = setup();
  elements.toggle.listeners.click();
  assert.equal(elements.fallback.hidden,false);
  elements.toggle.listeners.click();
  assert.equal(elements.fallback.hidden,true);
});
