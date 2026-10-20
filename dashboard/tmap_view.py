"""TMAP JS V2 지도 컴포넌트. 키와 좌표는 실행 중인 브라우저에만 전달합니다."""

from __future__ import annotations

import json
from html import escape
from pathlib import Path
from urllib.parse import urlencode


def build_tmap_html(payload: dict, app_key: str, fallback_svg: str, compact: bool = False) -> str:
    """스크립트 문맥을 벗어나는 입력을 차단하고 지도 템플릿을 생성합니다."""
    data = json.dumps({"payload": payload, "appKey": app_key, "compact": compact}, ensure_ascii=False)
    data = data.replace("&", "\\u0026").replace("<", "\\u003c").replace(">", "\\u003e")
    script = Path(__file__).with_name("tmap_map.js").read_text(encoding="utf-8")
    height = 340 if compact else 520
    # JS V2 로더는 document.write로 하위 SDK를 불러올 수 있습니다.
    # 동적 appendChild 대신 HTML 파서가 읽는 정적 script 태그로 순서를 보장합니다.
    sdk_url = "https://apis.openapi.sk.com/tmap/jsv2?" + urlencode({"version": "1", "appKey": app_key.strip()})
    sdk_tag = f'<script src="{escape(sdk_url, quote=True)}"></script>' if app_key.strip() else ""
    return f'''<!doctype html>
<html lang="ko"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
*{{box-sizing:border-box}} body{{margin:0;font:14px system-ui,sans-serif;color:#334155}}
.toolbar{{display:flex;align-items:center;gap:8px;min-height:44px;flex-wrap:wrap}}
.toolbar span{{flex:1}} button{{padding:6px 10px;border:1px solid #cbd5e1;border-radius:8px;background:white;cursor:pointer}}
#map{{width:100%;height:{height}px;border:1px solid #e2e8f0;border-radius:12px;overflow:hidden}}
#fallback{{height:{height}px;overflow:auto}} [hidden]{{display:none!important}}
#notice{{margin:4px 0 8px;font-size:12px;color:#64748b}}
</style></head><body>
<div class="toolbar"><strong>TMAP</strong><span id="status" role="status">지도 연결 중…</span>
<button id="fit" type="button" disabled>전체 보기</button>
<button id="retry" type="button" hidden>다시 연결</button>
<button id="toggle" type="button">좌표 보기</button></div>
<p id="notice" role="alert">차량 · 요청 출발/도착 · 이동 경로를 표시합니다.</p>
<div id="map" aria-label="TMAP 시뮬레이션 지도"></div>
<div id="fallback" hidden>{fallback_svg}</div>
<script id="tmap-data" type="application/json">{data}</script>
{sdk_tag}
<script>{script}</script>
</body></html>'''
