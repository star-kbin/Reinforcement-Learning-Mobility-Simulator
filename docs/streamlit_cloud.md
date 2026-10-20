# Streamlit Community Cloud 배포 안내

이 문서는 `main` 브랜치의 최신 **OpenStreetMap(OSM) 기반 시뮬레이터**를 Streamlit Community Cloud로 배포하는 절차를 설명합니다.

## 배포 대상

| 항목 | 값 |
| --- | --- |
| GitHub 저장소 | `star-kbin/Reinforcement-Learning-Mobility-Simulator` |
| 브랜치 | `main` |
| 앱 진입점 | `dashboard/app.py` |
| Python 의존성 | 루트 `requirements.txt` |
| 지도 | OpenStreetMap 타일 + Leaflet |
| 도로 경로 | 저장소 내장 OSM 그래프 + Dijkstra |
| 외부 지도 API 키 | 필요 없음 |

## 최초 배포

1. [Streamlit Community Cloud](https://share.streamlit.io/)에 접속하고 GitHub 계정으로 로그인합니다.
2. **Create app / New app**에서 **Deploy a public app from GitHub**를 선택합니다(화면 문구는 변경될 수 있습니다).
3. 저장소 `star-kbin/Reinforcement-Learning-Mobility-Simulator`, 브랜치 `main`, 파일 경로 `dashboard/app.py`를 지정합니다.
4. 앱 이름과 공개 URL을 설정하고 **Deploy**를 실행합니다.
5. 앱이 정상 로딩될 때까지 로그를 확인하고, 시뮬레이션 실행 및 OSM 경로 표시까지 테스트합니다.
6. 발급된 **실제** `https://...streamlit.app` URL을 복사하여 루트 [README.md](../README.md)의 **웹에서 시뮬레이터 실행하기** 표의 첫 행에 붙여 넣고 상태를 **배포 완료**로 수정합니다.

> GitHub 저장소에 파일을 추가하는 것만으로 Streamlit Cloud 앱이 자동 생성되지는 않습니다. Streamlit 서비스에서 최초 배포가 완료되어야 실제 접속 URL이 생깁니다.

## 공개 전 점검표

- [ ] `dashboard/app.py` 로딩 및 라이브러리 설치가 정상적으로 완료됨
- [ ] 시뮬레이션 실행 버튼과 정책별 결과 비교가 정상 동작함
- [ ] 배경 OSM 타일이 보이고 저작자 표시가 유지됨
- [ ] 도로 경로가 내장 OSM 데이터로 계산되며 지원 범위 밖 오류가 정상 안내됨
- [ ] 모바일/좁은 화면에서 핵심 정보 확인 가능
- [ ] 앱이 잠자기 상태에서 재시작한 후에도 정상 실행됨
- [ ] README의 실제 공개 링크로 외부 브라우저에서 접근 가능

## 장애 대응

- **앱이 실행되지 않을 때:** Streamlit Cloud의 앱 로그에서 Python import 오류, 실행 파일 경로, `requirements.txt` 설치 여부를 확인합니다.
- **지도가 빈 화면일 때:** 브라우저의 외부 지도 타일·Leaflet 접근을 확인합니다. 외부 지도가 차단될 경우 좌표 대체 화면을 사용합니다.
- **경로를 찾지 못할 때:** OSM 내장 그래프의 지원 영역(위도 37.475~37.535, 경도 126.915~127.055)과 도로 연결 상태를 확인합니다.
- **업데이트가 반영되지 않을 때:** 배포 앱이 `main` 브랜치의 최신 커밋을 바라보는지 확인하고 Streamlit Cloud에서 재부팅/재배포 상태를 살펴봅니다.

내장 그래프 관련 세부 사항은 [OSM 데이터 설명](../data/osm/README.md)을 참고하세요.
