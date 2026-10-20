# 강화학습 모빌리티 시뮬레이터

> **OSM(OpenStreetMap) 기반 차량 배차·경로 시뮬레이션 | Streamlit 웹 대시보드**

차량 배차 정책을 비교하고 차량·요청·이동 경로를 지도에서 확인하는 Python/Streamlit 학습용 앱입니다.

## 🌐 웹에서 시뮬레이터 실행하기

| 항목 | 링크 | 상태 |
| --- | --- | --- |
| **Streamlit Cloud 온라인 데모** | [🚀 OSM 모빌리티 시뮬레이터 실행](https://reinforcement-learning-mobility-simulator.streamlit.app/) | 공개 배포 URL 등록 |
| **최신 OSM 버전 소스코드** | [dashboard/app.py](dashboard/app.py) | GitHub에서 확인 가능 |
| **Streamlit 배포 가이드** | [배포 설정 및 점검 방법](docs/streamlit_cloud.md) | 안내 문서 |
| **OSM 지도 데이터** | [데이터 범위 및 출처](data/osm/README.md) | GitHub에서 확인 가능 |

**온라인 데모:** [OSM 모빌리티 시뮬레이터 바로 실행](https://reinforcement-learning-mobility-simulator.streamlit.app/) · [Streamlit Cloud 앱 관리](https://share.streamlit.io/)

### 처음 방문한 분들을 위한 사용 순서

1. Streamlit 앱이 공개 배포되면 위 **온라인 데모** 링크로 접속합니다.
2. 배차 정책(`nearest`, `eta`, `random`), 차량 수, 요청 수와 실행 시간을 설정합니다.
3. **시뮬레이션 실행**을 눌러 지도에서 차량 위치와 계획/실제 경로를 확인합니다.
4. 타임라인을 재생하고 완료 요청 수·대기 시간·차량 이동 거리를 비교합니다.

기본 지도 배경은 온라인 OSM 타일을 사용하고, **차량 경로 계산은 저장소에 포함된 OSM 도로망으로 수행**합니다. 지도 배경이 차단되어도 좌표 기반 대체 화면을 제공합니다. 지원 도로망 범위는 서울 동작·서초 및 인접 강남 실습 지역으로 한정됩니다.

---

## 빠른 시작

```bash
python -m pip install -r requirements.txt
streamlit run dashboard/app.py
```

1. 차량 수, 요청 수, 실행 시간을 조정합니다.
2. **시뮬레이션 실행**을 누릅니다.
3. OSM 지도에서 차량·승하차 위치·계획 경로·실제 이동 경로를 확인합니다.
4. 타임라인을 재생하고 정책별 완료 요청 수, 대기 시간, 이동 거리를 비교합니다.

API 키 발급이나 Secrets 설정은 필요하지 않습니다. 지도는 **OpenStreetMap + Leaflet**, 위경도 경로 계산은 **저장소에 포함한 OSM 도로망 + Dijkstra**를 사용합니다. 이전 지도 SDK와 API 키 입력·환경변수·경로 API 호출을 제거했습니다. 이전 프리셋의 지도/라우팅 선택은 OSM으로 전환하고 `integrations`는 화면 저장에서 제거합니다.

기본 화면에는 정책·차량 수·요청 수·실행 시간만 표시합니다. **상세 설정**에서 개별 좌표, 일정 평균 속도, 요청, 보상, 프리셋, 배치 실험을 조정할 수 있습니다. 간편/상세 전환 시 입력을 보존하며, 요청은 수를 바꾸거나 **요청 다시 생성**을 누를 때만 바뀝니다.

## 지도와 경로의 동작

| 기능 | 구현 | 인터넷 필요 |
| --- | --- | --- |
| 배경 지도 | OpenStreetMap 표준 타일, Leaflet | 필요 |
| 자동차 도로 경로 | 내장 OSM 방향 그래프, 최단 거리 탐색 | 불필요 |
| 소요 시간 | 도로·연결 거리 ÷ 설정 평균 속도 | 불필요 |
| 지도 연결 실패 시 | 차량·요청·경로를 SVG 좌표 화면으로 표시 | 불필요 |
| 주소 검색 | Nominatim, 사용자가 검색할 때만 요청 | 필요 |

OSM 타일은 지도 배경입니다. 경로는 타일이나 공용 경로 서버에 의존하지 않고 앱에서 계산합니다. 경로 캐시는 서비스별 최대 512개이며, 도로 그래프는 프로세스에서 재사용합니다. 지도 로딩이 지연되면 최대 15초 후 좌표 보기로 전환하고, 타일이 도착하면 지도 표시를 복구합니다. Leaflet 파일 자체가 차단돼도 좌표 보기를 제공합니다.

### 기본 도로망 범위

서울 동작·서초 및 인접 강남 실습 지역: **위도 37.475~37.535, 경도 126.915~127.055**. 기본 강남 시작점과 동작구 시나리오를 포함합니다. 자세한 출처·재생성 방법은 [OSM 데이터 안내](data/osm/README.md)를 참고하세요.

- 일방통행과 자동차 접근 제한을 반영합니다. 끊긴 도로를 임의로 역방향 연결하지 않습니다.
- 입력 좌표 주변 최대 500m 안에서 도로 후보를 찾습니다. 입력 위치와 도로 사이 짧은 진입·진출 연결선은 도로 구간과 구분해 `connector_distance_meters`에 기록합니다.
- 지도에 보이는 모든 지역을 경로 탐색할 수 있는 것은 아닙니다. 범위 밖 좌표, 가까운 자동차 도로 부재, 연결 경로 부재는 오류로 안내하며 직선 도로 경로로 대체하지 않습니다.
- 실시간 교통, 회전 제한 관계, 차선 안내, 시간별 통행 제한은 반영하지 않습니다. 실제 운전용 내비게이션이 아닌 정책 비교 학습 도구입니다.
- 공용 지도 타일·주소 검색 서비스의 가용성은 보장되지 않습니다. 이 경우에도 내장 도로 경로 계산은 가능합니다.

### 설정 예시

```yaml
simulation:
  space_mode: latlon
  routing_provider: osm
  osm_average_speed_kph: 25.0
  osm_max_snap_meters: 500.0
  # 다른 지역 그래프를 준비한 경우 선택 지정
  # osm_graph_path: data/osm/my_region.json.gz.b64
visualization:
  map_provider: openstreetmap
```

`grid` 시나리오는 기존 격자 이동을 유지합니다. 과거 `local_its` 설정도 위경도 모드에서는 OSM 계산으로 전환됩니다. 평균 속도는 `osm_average_speed_kph`로 지정합니다.

## 명령행 실행

```bash
python scripts/run_simulation.py --config configs/base_latlon.yaml --scenario scenarios/scenario_dongjak_history.yaml --policy nearest --output results/osm_demo.json
python scripts/run_simulation.py --config configs/base.yaml --scenario scenarios/scenario_simple_01.yaml --policy nearest
```

정책: `nearest`는 좌표상 가까운 차량, `eta`는 기존 간이 ETA 기준, `random`은 임의의 유효 배차입니다. 정책의 차량 선택 추정과 실제 OSM 주행 시간은 다를 수 있습니다. 학습 알고리즘을 자동 훈련하는 앱은 아니며 baseline 정책 비교와 확장을 위한 환경입니다.

요청은 `waiting → assigned → picked_up → completed`로 이동하고 대기 제한을 넘으면 취소됩니다. 에피소드는 최대 시간 도달 또는 모든 요청 처리·취소 시 종료합니다. 보상은 대기/이동 패널티, 완료 보너스, 취소 패널티 중심입니다. `violation` 설정은 현재 계산에 직접 합산되지 않습니다.

## 검증

```bash
python -m unittest discover -s tests -p "test_*.py" -v
```

검증 범위: 실제 내장 도로망 경로, 일방통행, 연결 실패/범위 오류, 캐시, 외부 네트워크 없이 기본 에피소드 실행, 간편/상세 입력 보존, 요청 재생성, 지도 HTML 안전 인코딩, 격자 모드 실행. 실행 결과에 `osm_offline` 출처와 도로 거리·연결 거리 메타데이터가 기록됩니다.

## 디렉터리

```text
dashboard/          Streamlit 화면·OSM 지도·좌표 보기
simulator/          환경, 차량, 요청, 정책, 보상, 평가
simulator/osm_routing.py  내장 OSM 방향 그래프 경로 탐색
data/osm/           OSM 도로망 및 데이터 출처
configs/            실행 설정
scenarios/          요청 시나리오
scripts/            명령행 실행·시나리오/도로망 생성
tests/              실행 및 회귀 검증
results/            실행 결과·프리셋
```

지도에는 [OpenStreetMap 저작자 표시](https://www.openstreetmap.org/copyright)를 유지합니다. [OSM 타일 이용 정책](https://operations.osmfoundation.org/policies/tiles/)에 따라 일반 지도 표시만 수행하며 대량 다운로드·오프라인 타일 저장을 하지 않습니다.
