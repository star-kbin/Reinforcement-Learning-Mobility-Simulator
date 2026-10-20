# 서울 실습용 OSM 자동차 도로망

- 출처: © OpenStreetMap contributors, [OpenStreetMap](https://www.openstreetmap.org/copyright), [ODbL 1.0](https://opendatacommons.org/licenses/odbl/1-0/)
- 추출일: 2026-10-20 (실행 환경 기준)
- 추출 서비스: https://overpass-api.de/api/interpreter
- 범위: 남 37.475, 서 126.915, 북 37.535, 동 127.055
- 내용: 노드 46,631개, 방향 간선 94,870개. 각 노드의 위경도와 자동차 도로의 연결 관계.
- 저장: UTF-8 JSON을 gzip(mtime=0) 후 base64로 인코딩한 `seoul_demo.json.gz.b64`. 앱은 표준 라이브러리로 해제합니다.
- 해당 파일은 OSM에서 추출·변환한 파생 데이터베이스이며 ODbL 1.0 조건으로 제공합니다. 원자료와 동일한 저작자 표시 및 해당 라이선스 조건을 유지해 주세요.

## 재생성

Overpass에서 아래 쿼리를 한 번 실행해 JSON을 저장한 후 변환합니다. 앱 실행 중 Overpass를 호출하지 않습니다. 범위를 바꿀 때는 변환 스크립트의 bounds도 일치시킵니다.

```text
[out:json][timeout:90];
way["highway"]["highway"!~"footway|path|cycleway|steps|pedestrian|construction|proposed|bridleway|track"]
["access"!="private"]["motor_vehicle"!="no"](37.475,126.915,37.535,127.055);
(._;>;);
out body;
```

```bash
python scripts/prepare_osm_graph.py osm_extract.json data/osm/seoul_demo.json.gz.b64
```

변환은 access=no/private, motor_vehicle=no/private, motorcar=no를 제외합니다. oneway=yes/1/true/-1, roundabout 및 motorway 기본 방향을 반영합니다. 교차로 회전 제한 관계는 포함하지 않습니다. 전체 노드 좌표를 보존하여 도로 형상을 표시합니다. 도로망 경계에서는 범위 밖으로 우회하는 경로를 찾을 수 없습니다.

도로의 품질·최근 변경 여부·실제 통행 가능 여부를 보장하지 않습니다. 기본 시나리오를 위한 교육용 데이터이며 실제 운전 안내에 사용하지 않습니다.
