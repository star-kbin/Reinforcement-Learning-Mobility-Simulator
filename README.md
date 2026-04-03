# Reinforcement Learning Mobility Simulator

강화학습 기반 모빌리티 배차/이동 실험을 위한 시뮬레이터 및 대시보드입니다.
현재 버전은 `로컬 ITS 도로망 + 시간대별 평균 속도 + step 기반 환경`을 중심으로, 다음 두 가지를 빠르게 반복할 수 있도록 구성되어 있습니다.

- 정책 비교: `random`, `nearest`, `eta`
- RL 환경 설계 점검: 상태, 행동, 보상, 종료 조건, 시나리오, 결과 분석 흐름 정리

대시보드를 통해 초기 상태를 조정하고 시뮬레이션 결과를 지도와 타임라인으로 확인할 수 있으며, CLI로 단일 에피소드 실행 결과를 JSON으로 저장할 수도 있습니다.

## 1. 현재 프로젝트가 제공하는 것

### 1.1 공간/이동 모델

- 기본 좌표계는 `lat/lon`
- 기본 라우팅 백엔드는 `local_its`
- 로컬 ITS 그래프와 도로 geometry를 사용해 실제 도로 형상을 따라 이동 경로를 생성
- 이동 시간은 아래 두 방식 중 하나로 계산
  - 고정 평균 속도 `ITS 평균 속도 (km/h)`
  - 24시간 속도표 기반 시간대별 속도
- 시간 기준은 현재 `한국 시간(KST)`으로 고정

### 1.2 정책 비교

현재 내장 baseline 정책:

- `random`: 가능한 행동 중 임의 선택
- `nearest`: 가장 가까운 차량 기준 배차
- `eta`: 예상 도착시간이 가장 짧은 차량 기준 배차

### 1.3 시뮬레이션 환경

현재 환경은 다음 흐름으로 동작합니다.

1. 시작 시각 `window_start`에서 에피소드 시작
2. 각 step마다 요청 활성화
3. 정책이 현재 상태에서 행동 선택
4. 차량 이동 및 승하차 처리
5. 보상 계산
6. 종료 조건 확인
7. 타임라인/로그 저장

관련 핵심 구현:

- 환경: [env.py](/home/moduletest/project/RL_simulator/simulator/env.py)
- 라우팅: [routing.py](/home/moduletest/project/RL_simulator/simulator/routing.py)
- 보상: [weighted_reward.py](/home/moduletest/project/RL_simulator/simulator/rewards/weighted_reward.py)
- 행동 공간: [action_space.py](/home/moduletest/project/RL_simulator/simulator/action_space.py)
- 상태 인코더: [state_encoder.py](/home/moduletest/project/RL_simulator/simulator/state_encoder.py)

## 2. 현재 RL 관점에서의 환경 정의

### 2.1 상태(State)

현재 정책 입력 상태는 단순 dict 구조입니다.

- 현재 시각 `current_time`
- 차량 리스트
- 요청 리스트

차량에는 위치, 상태, 용량, 현재 적재, 배정 요청 등이 포함되고, 요청에는 출발지/도착지, 상태, 대기 시간 등이 포함됩니다.

즉, 현재는 “학습용으로 바로 쓰기 쉬운 벡터형 인코더”보다는 “정책/디버깅 친화적 객체형 상태”에 가깝습니다.

### 2.2 행동(Action)

현재 실제 행동 공간은 매우 단순합니다.

- `WAIT`
- `ASSIGN(vehicle_id, request_id)`

즉, MVP 기준으로는 “대기 중 요청을 유휴 차량에 배차할지 여부”가 핵심입니다.  
문서상 재배치(reposition) 방향은 열려 있지만, 현재 action space에는 직접 포함되어 있지 않습니다.

### 2.3 보상(Reward)

현재 step 보상은 가중합 구조입니다.

`Reward = - waiting_penalty - distance_penalty + completed_bonus - cancelled_penalty`

구성 항목:

- 대기 패널티: `waiting`, `assigned` 상태 요청 수 기반
- 이동 패널티: 이번 step 이동거리 기반
- 완료 보너스: 이번 step 완료 요청 수 기반
- 취소 패널티: 이번 step 취소 요청 수 기반

주의:

- `violation` 가중치 필드는 config에 있지만, 현재 `WeightedRewardCalculator`에서 직접 합산되지는 않습니다.
- 따라서 현재 RL 보상은 “대기/이동/완료/취소” 중심입니다.

### 2.4 종료 조건

에피소드는 아래 중 하나를 만족하면 종료됩니다.

- `max_time` 도달
- 모든 요청이 `completed` 또는 `cancelled`

## 3. 대시보드에서 설정 가능한 실험 요소

대시보드 파일:

- [app.py](/home/moduletest/project/RL_simulator/dashboard/app.py)

### 3.1 실행 설정

- Config 선택
- Scenario 선택
- 정책 선택
- 지도 표시 방식 선택
- TMAP 키 입력
  현재 기본 권장 흐름은 `local_its` 중심이며, TMAP은 선택적 백엔드입니다.

### 3.2 환경 설정

- Max Time
- Time Step
- 라우팅 백엔드
- 시뮬레이션 시작 시각
- 시작 시각 기준 경로 사용 여부
- ITS 평균 속도
- 24시간 속도표 사용 여부
- 시간대별 속도표 입력

### 3.3 차량 설정

- 차량 수
- 용량
- 가용 시간
- 시작 위도/경도
- 주소 검색 기반 시작 좌표 적용

### 3.4 요청 설정

- 요청 수
- 차량 근방 생성 반경
- 랜덤 요청 재생성
- 생성된 요청 목록 표

현재 요청은 “차량 시작 위치 주변에서 origin/destination이 겹치지 않도록 랜덤 생성”하는 방식으로 빠르게 실험할 수 있습니다.

### 3.5 초기 상태 편집

초기 상태 영역에서 다음을 할 수 있습니다.

- 차량 시작 위치 선택
- 요청 출발지/도착지 선택
- 상/하/좌/우 버튼으로 미세 이동
- 이동 간격과 이동 레벨 변경

즉, RL 학습 전에 “이 초기 상태가 정책 비교에 적절한가?”를 바로 눈으로 맞추는 용도로 매우 유용합니다.

## 4. 시뮬레이션 결과로 확인할 수 있는 것

### 4.1 요약 지표

현재 결과 요약에서 바로 볼 수 있는 대표 값:

- 총 Reward
- 완료 요청 수
- 취소 요청 수
- 성공률
- 실제 이동거리(km)
- 평균 속도(km/h)
- 시뮬레이션 총 시간
- 차량 운행 시간

### 4.2 보상 해석

대시보드에는 총 Reward 아래에 다음 합산값이 함께 표시됩니다.

- 대기 페널티 합
- 이동 페널티 합
- 완료 보너스 합
- 취소 페널티 합

즉, “보상이 왜 그렇게 나왔는지”를 정책별로 분해해서 볼 수 있습니다.

### 4.3 지도 기반 결과 확인

결과 지도에서는 다음을 확인할 수 있습니다.

- 차량 시작 위치
- 차량 현재 위치
- 요청 출발지/도착지
- 요청 상태 변화
- 도로망 경로
- 계획 경로 이력
- 실제 주행 누적 경로

또한 타임라인을 step 단위로 이동하거나 자동 재생하여,

- 요청 발생
- 대기
- 탑승 이동
- 도착 완료

흐름을 시간 순서대로 점검할 수 있습니다.

### 4.4 RL 실험에서 이 결과가 의미하는 것

현재 대시보드 결과는 RL 관점에서 다음 질문에 답하는 데 유용합니다.

- 이 보상 설계가 원하는 방향으로 정책을 유도하는가?
- 특정 정책이 왜 성공률은 높지만 Reward는 낮은가?
- 이동거리 절감과 대기시간 절감 사이 trade-off가 있는가?
- 출발지/도착지 분포가 정책 성능에 어떤 영향을 주는가?
- 시간대별 속도표가 배차 결과를 얼마나 바꾸는가?

## 5. 현재 프로젝트 기준 RL 전반 플로우

현재 이 프로젝트로 추천하는 전체 흐름은 아래와 같습니다.

### 5.1 1단계: 시나리오/환경 구성

- Config 선택
- 차량 수, 시작 위치, 가용 시간 설정
- 요청 수와 생성 반경 설정
- ITS 평균 속도 또는 24시간 속도표 설정
- 시작 시각 설정

이 단계 목표:

- 학습/평가에 사용할 환경 분포를 정의

### 5.2 2단계: baseline 정책으로 sanity check

먼저 `random`, `nearest`, `eta`를 돌려서 아래를 확인합니다.

- 성공률이 0% 또는 100%로만 나오지 않는가
- Reward가 직관과 크게 어긋나지 않는가
- 차량이 실제로 도로 geometry를 따라 움직이는가
- 초기 상태가 너무 쉽거나 너무 어렵지 않은가

이 단계 목표:

- RL 전에 환경과 보상 자체가 정상인지 검증

### 5.3 3단계: RL 학습 환경 연결

현재 코드 기준 RL 연결 포인트는 아래입니다.

- `env.reset()`
- `env.get_state()`
- `env.get_valid_actions()`
- `env.step(action)`
- `env.is_done()`
- `env.get_result()`

즉, 현재 구조는 baseline 비교용으로도 쓰이지만, RL agent를 붙이기 위한 최소 loop도 갖추고 있습니다.

### 5.4 4단계: 학습 목표 정의

현재 프로젝트에서 RL 목표는 보통 아래 중 하나로 잡을 수 있습니다.

- 완료 요청 수 최대화
- 대기시간 최소화
- 이동거리 최소화
- 성공률과 비용의 균형 최적화

실제로는 reward weight 조합을 바꾸며 어떤 정책 성향이 생기는지 먼저 baseline으로 본 뒤, RL 학습으로 이어가는 것이 가장 안정적입니다.

### 5.5 5단계: 학습 후 비교/분석

학습된 정책은 baseline과 동일 조건으로 반복 비교해야 합니다.

비교 권장 항목:

- 총 Reward
- 성공률
- 평균/최대 대기시간
- 총 이동거리
- 차량 운행시간
- 시간대별 성능 차이

결과를 지도와 step 타임라인으로 다시 보면 “왜 그 행동을 했는지”를 정성적으로 검토할 수 있습니다.

## 6. 현재 코드 기준 실행 방법

### 6.1 대시보드 실행

```bash
bash /home/moduletest/project/RL_simulator/scripts/run_dashboard.sh
```

### 6.2 CLI 단일 시뮬레이션 실행

```bash
python3 /home/moduletest/project/RL_simulator/scripts/run_simulation.py \
  --config /home/moduletest/project/RL_simulator/configs/base_latlon.yaml \
  --scenario /home/moduletest/project/RL_simulator/scenarios/scenario_dongjak_history.yaml \
  --policy nearest
```

결과를 JSON으로 저장하려면:

```bash
python3 /home/moduletest/project/RL_simulator/scripts/run_simulation.py \
  --config /home/moduletest/project/RL_simulator/configs/base_latlon.yaml \
  --scenario /home/moduletest/project/RL_simulator/scenarios/scenario_dongjak_history.yaml \
  --policy eta \
  --output /home/moduletest/project/RL_simulator/results/example_result.json
```

### 6.3 이력 CSV에서 시나리오 생성

```bash
python3 /home/moduletest/project/RL_simulator/scripts/build_scenario_from_history.py \
  --input /home/moduletest/project/STDN_share/data/dongjak_OD_station_lat_lon.csv \
  --output /home/moduletest/project/RL_simulator/scenarios/scenario_dongjak_history.yaml \
  --config-output /home/moduletest/project/RL_simulator/configs/dongjak_history_latlon.yaml
```

### 6.4 여러 조건 자동 실험 및 레포트 생성

여러 조건 조합을 자동으로 돌리고, 결과를 CSV/Markdown 레포트로 묶으려면 `suite yaml`을 사용합니다.

예시 suite:

- [single_vehicle_baseline.yaml](/home/moduletest/project/RL_simulator/experiment_suites/single_vehicle_baseline.yaml)

실행:

```bash
python3 /home/moduletest/project/RL_simulator/scripts/run_experiment_suite.py \
  --suite /home/moduletest/project/RL_simulator/experiment_suites/single_vehicle_baseline.yaml
```

출력:

- `run_results.csv`: 개별 실행 결과 1행씩
- `policy_summary.csv`: 정책별 평균 요약
- `report.md`: Markdown 보고서
- `runs/*.json`: 각 실험 상세 payload

현재 suite에서 바로 바꿔볼 수 있는 대표 조건 축:

- `vehicles.count`
- `simulation.window_start`
- `simulation.time_step`
- `simulation.local_its_use_hourly_speed_profile`
- `request_generation.request_count`
- `request_generation.radius`
- `request_generation.max_wait_time`
- `request_generation.seed`

즉, 대시보드에서 손으로 바꾸던 조건을 배치 실험으로 옮겨서 baseline 비교와 RL 학습 전 분포 점검을 자동화할 수 있습니다.

## 7. 현재 프로젝트로 얻을 수 있는 대표 산출물

이 프로젝트로 현재 직접 얻을 수 있는 산출물은 다음과 같습니다.

- 시나리오별 정책 성능 비교 결과
- step 타임라인 로그
- 요청별 상태 변화 이력
- 차량별 이동 경로 이력
- 보상 항목별 분해 결과
- 지도 기반 정성 분석 결과
- RL 학습 전 환경 sanity check 결과

즉, 현재 버전은 “완성된 대규모 RL 학습 시스템”이라기보다,

- RL 문제 정의를 검증하고
- baseline과 비교하며
- 시나리오/보상/속도표/초기 상태를 빠르게 조정하고
- 학습 전에 환경을 안정화하는 실험 플랫폼

으로 보는 것이 가장 정확합니다.

## 8. 현재 한계와 해석 시 주의점

현재 버전 사용 시 아래 사항은 반드시 알고 있어야 합니다.

### 8.1 행동 공간 한계

- 실제 action space는 아직 `WAIT`, `ASSIGN` 중심
- 재배치(reposition)는 대시보드 초기 상태 편집과는 별개이며, RL 행동으로는 직접 구현되지 않음

### 8.2 보상 한계

- `violation` 가중치는 현재 직접 반영되지 않음
- 대기 패널티는 “실제 초 단위 누적”이 아니라 현재 step에서 대기/배정 상태 요청 수 기반

### 8.3 교통 모델 한계

- `local_its`는 실제 도로망 geometry를 사용하지만, 속도는 현재
  - 고정 평균 속도
  - 또는 24시간 hour-level 평균 속도
  기반
- 링크별 실시간 속도까지는 아직 아님

### 8.4 지도 해석 주의

- 시작/종료 좌표가 도로 중심선과 다르면 짧은 connector가 보일 수 있음
- 이는 도로 geometry가 틀린 경우가 아니라 좌표 스냅 연결 구간일 수 있음

## 9. 다음 단계 권장

현재 프로젝트를 RL 연구/개발로 확장하려면 다음 순서를 권장합니다.

1. baseline 성능 분포를 시나리오별로 충분히 수집
2. reward 항목을 실제 목표에 맞게 재정의
3. state를 학습 친화적 tensor/vector 형태로 추가 인코딩
4. action mask와 후보 축소 전략 정교화
5. 학습 루프(PPO/DQN/actor-critic 등) 연결
6. baseline 대비 improvement를 KPI와 지도 재생으로 동시 검증

---

현재 대시보드는 “환경 설계 -> baseline sanity check -> RL 연결 준비 -> 결과 해석”까지의 전 과정을 빠르게 반복하기 위한 실험용 인터페이스로 보는 것이 가장 적합합니다.
