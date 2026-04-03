# 강화학습 기반 모빌리티 Simulator 요구사항 초안

## 1. 문서 목적

본 문서는 강화학습(RL) 기반 모빌리티 경로 선정 및 차량 배차 최적화 실험을 위한 초기 버전 시뮬레이터의 요구사항을 정의한다.

초기 버전은 다음 목표를 가진다.

- 2대 차량 기준의 단순한 환경에서 정책별 배차 및 경로 차이를 비교할 수 있어야 한다.
- 상태, 행동, 보상, 제약조건을 명확히 정의하여 RL 학습 환경으로 연결 가능해야 한다.
- MVP 수준에서 빠르게 동작하되, 이후 차량 수 증가, 합승, 교통 상황, 실제 도로망 등으로 자연스럽게 확장 가능해야 한다.


## 2. 개발 목표

초기 버전 시뮬레이터의 핵심 목표는 다음과 같다.

- 차량 2대와 다수의 승객 요청이 존재하는 환경을 시뮬레이션한다.
- 정책에 따라 차량 배차 결과와 이동 경로가 어떻게 달라지는지 비교할 수 있다.
- 승객 대기 시간, 차량 이동 거리, 서비스 성공률 등 핵심 성능 지표를 계산할 수 있다.
- 규칙 기반(Baseline) 정책과 강화학습 정책을 같은 환경에서 비교할 수 있다.
- 환경 설정, 수요 시나리오, 보상 함수, 제약조건을 변경 가능한 구조로 설계한다.


## 3. MVP 범위

초기 MVP는 복잡도를 제한하면서도 정책 비교가 가능한 최소 기능 집합으로 정의한다.

### 3.1 공간 모델

- 공간은 우선 `2D grid` 또는 `간단한 node-edge graph` 중 하나로 구현한다.
- 초기 버전에서는 실제 지도 연동 없이 추상화된 이동 공간을 사용한다.
- 차량과 승객 위치는 좌표 또는 노드 ID로 표현한다.
- 두 지점 간 이동 시간은 거리와 차량 속도를 기반으로 계산한다.

권장안:

- MVP는 구현 난이도와 시각화를 고려하여 `2D grid` 기반으로 시작한다.
- 이후 확장 버전에서 실제 도로 그래프 또는 지도 데이터 기반 이동 모델을 추가한다.

### 3.2 시간 모델

- 시뮬레이터는 `이벤트 기반(event-driven)` 또는 `고정 time-step` 중 하나로 동작해야 한다.
- MVP에서는 구현 단순성과 디버깅 편의성을 위해 `고정 time-step` 방식을 우선 적용한다.
- 이후 확장 버전에서 호출 발생, 차량 도착, 승하차 완료 시점 중심의 `SMDP/event-driven` 구조로 고도화할 수 있어야 한다.

MVP 기본값:

- 1 step = 1 unit time
- 각 step마다 요청 생성, 차량 이동, 승하차 처리, 보상 계산, 종료 여부 판정 수행

### 3.3 차량 모델

- 기본 차량 수는 2대로 한다.
- 각 차량은 다음 속성을 가진다.
  - 차량 ID
  - 현재 위치
  - 현재 상태: idle / to_pickup / onboard / unavailable
  - 현재 탑승 인원
  - 최대 수용 인원
  - 제한 속도
  - 남은 가용 시간
  - 현재 할당된 요청 목록
- 차량 시작 위치는 시뮬레이션 시작 전에 설정 가능해야 한다.
- 필요 시 각 차량의 초기 목적지 또는 초기 배치 지역을 설정할 수 있어야 한다.

MVP 단순화 조건:

- 차량 속도는 기본적으로 고정값 사용
- 모든 차량은 동일한 성능을 가진다고 가정
- 충전, 연료, 정비 상태는 초기 버전에서 제외

### 3.4 승객 수요 모델

- 각 요청(Request)은 다음 정보를 가진다.
  - 요청 ID
  - 발생 시각
  - 출발 위치
  - 도착 위치
  - 최대 허용 대기 시간
  - 상태: waiting / assigned / onboard / completed / cancelled
- MVP에서는 요청 리스트를 사전 정의하여 입력할 수 있어야 한다.
- 추가적으로 랜덤 요청 생성 기능을 옵션으로 제공할 수 있어야 한다.

MVP 권장 방식:

- 고정 시나리오 입력 파일(JSON/YAML) 기반 요청 생성
- 선택적으로 랜덤 seed 기반 요청 샘플링 지원

### 3.5 탑승 정책

- 초기 버전에서는 `합승 없음`을 기본으로 한다.
- 차량 1대는 한 시점에 1개 요청만 서비스하는 단순 모델을 우선 적용한다.
- 이후 확장 시 다인승 및 합승 허용 구조를 추가할 수 있어야 한다.

### 3.6 행동(Action) 정의

MVP에서 에이전트 또는 정책은 다음 행동을 수행할 수 있어야 한다.

- 대기 중인 요청을 특정 차량에 할당
- 요청을 일시적으로 보류
- 유휴 차량을 특정 위치로 재배치
- 현재 경로를 유지

MVP 단순 행동안:

- `assign(vehicle_id, request_id)`
- `reposition(vehicle_id, target_position)`
- `wait`

주의사항:

- 행동 공간이 너무 커지지 않도록 MVP에서는 가능한 action mask를 적용한다.
- 제약조건상 불가능한 행동은 선택 불가 상태로 처리하거나 큰 penalty를 부여한다.

### 3.7 상태(State) 정의

정책 입력용 상태는 최소한 다음 정보를 포함해야 한다.

- 현재 시각
- 차량별 현재 위치
- 차량별 상태
- 차량별 남은 가용 시간
- 차량별 적재 상태
- 대기 중 요청의 수
- 각 요청의 위치, 목적지, 대기 시간
- 전체 또는 지역별 수요 분포 요약 정보

MVP 상태 표현 권장안:

- 고정 길이 벡터 형태
- 차량 수와 요청 수 상한을 두고 padding 처리
- 시각화와 디버깅을 위한 원본 객체 상태도 별도로 유지

### 3.8 보상(Reward) 정의

초기 버전은 단일 목표가 아닌 가중합 기반 보상 함수를 지원해야 한다.

예시:

`reward = -a * 승객대기시간 - b * 차량이동거리 + c * 완료요청수 - d * 제약위반 - e * 취소요청수`

최소 지원 항목:

- 승객 대기 시간 최소화
- 차량 총 이동 거리 최소화
- 완료된 요청 수 최대화
- 제약 위반 최소화

요구사항:

- 보상 항목별 가중치를 설정 파일로 변경 가능해야 한다.
- 학습용 reward와 평가용 KPI는 분리해야 한다.

### 3.9 제약조건

초기 버전은 다음 제약을 반영해야 한다.

- 차량 최대 수용 인원
- 차량별 가용 시간
- 승객 최대 대기 시간
- 차량 제한 속도

추가 권장 제약:

- 서비스 가능 영역 제한
- 요청 처리 불가 시 취소 규칙
- 차량별 근무 종료 시 신규 요청 배차 금지

### 3.10 종료 조건

에피소드는 다음 조건 중 하나를 만족하면 종료한다.

- 시뮬레이션 최대 시간 도달
- 모든 요청 처리 완료
- 모든 차량이 가용 시간 종료
- 최대 step 수 도달


## 4. 시스템 출력 및 분석 기능

초기 버전은 정책 비교와 디버깅을 위해 다음 결과를 제공해야 한다.

- 시간 순서별 차량 위치 변화
- 요청별 상태 변화 이력
- 차량별 배차 이력
- 최종 서비스 결과 요약
- 정책별 KPI 비교 테이블

최소 KPI:

- 평균 승객 대기 시간
- 최대 승객 대기 시간
- 총 차량 이동 거리
- 완료 요청 수
- 취소 요청 수
- 차량 가동률
- 공차 이동 거리 또는 공차 비율

권장 시각화:

- 시뮬레이션 step별 위치 로그
- 차량별 이동 경로 plot
- 요청 처리 타임라인
- 정책 간 KPI bar chart


## 5. 정책 비교 구조

초기 버전은 RL 정책뿐 아니라 baseline 정책을 함께 실행할 수 있어야 한다.

최소 baseline 후보:

- 랜덤 배차
- 최근접 차량 배차
- 최소 ETA 기반 배차
- 유휴 차량 고정 위치 대기 정책

요구사항:

- 동일한 입력 시나리오와 random seed에서 여러 정책을 반복 실행 가능해야 한다.
- 정책 간 결과를 동일 포맷으로 저장해야 한다.
- 결과 비교용 summary report를 생성할 수 있어야 한다.


## 6. 시나리오 및 설정 관리

초기 버전은 실험 재현성을 위해 설정 기반으로 동작해야 한다.

필수 설정 항목:

- 차량 수
- 차량 시작 위치
- 차량 최대 수용 인원
- 차량 속도
- 차량 가용 시간
- 요청 목록 또는 요청 생성 규칙
- 최대 시뮬레이션 시간
- reward weight
- random seed

권장 구현 방식:

- `config` 파일 기반 실행
- `scenario` 파일 기반 요청 세트 관리
- 결과 저장 디렉터리 구조 표준화

예시 디렉터리 구조:

- `configs/`
- `scenarios/`
- `results/`
- `logs/`


## 7. 소프트웨어 구조 요구사항

초기 버전은 이후 고도화를 고려하여 역할 분리가 필요하다.

권장 모듈 구조:

- `environment`
  - 상태 전이, step 처리, 종료 판정
- `entities`
  - vehicle, request 등 객체 정의
- `policies`
  - baseline 및 RL policy 인터페이스
- `rewards`
  - reward 계산 함수
- `constraints`
  - action feasibility 및 제약 검증
- `scenarios`
  - 입력 시나리오 로더
- `visualization`
  - 경로 및 KPI 시각화
- `evaluation`
  - 실험 결과 비교 및 리포트

정책 인터페이스 요구사항:

- 입력: 현재 상태
- 출력: 행동 또는 배차 결정
- baseline과 RL agent가 같은 인터페이스를 사용하도록 설계


## 8. 초기 API 요구사항

시뮬레이터는 최소한 다음 수준의 인터페이스를 제공해야 한다.

### 8.1 환경 인터페이스

- `reset(config, scenario)`
- `step(action)`
- `get_state()`
- `get_valid_actions()`
- `is_done()`
- `render()`
- `get_metrics()`

### 8.2 결과 인터페이스

- episode 단위 결과 저장
- 차량별 trajectory 저장
- 요청별 처리 로그 저장
- 정책별 KPI 요약 저장


## 9. 확장 가능하도록 반드시 고려할 사항

초기 버전 설계 시 아래 항목은 당장 구현하지 않더라도 구조적으로 확장 가능해야 한다.

### 9.1 차량 수 확장

- 2대에서 N대로 확장 가능해야 한다.
- 차량 수 증가 시 상태 표현과 행동 생성 로직이 깨지지 않아야 한다.

### 9.2 합승 및 다인승

- 향후 차량 하나가 여러 요청을 동시에 처리할 수 있도록 확장 가능해야 한다.
- 요청별 pickup/dropoff 순서 최적화 문제를 수용할 수 있어야 한다.

### 9.3 이벤트 기반 SMDP 전환

- 현재 time-step 기반 구조에서 이후 event-driven 구조로 바꾸기 쉽도록 설계한다.
- 내부 시계(clock)와 이벤트 큐 개념을 추가할 수 있는 구조가 바람직하다.

### 9.4 실제 도로망 및 교통 상황

- 단순 거리 기반 이동에서 향후 graph shortest path, 시간대별 혼잡도, edge travel time 변화 등을 반영할 수 있어야 한다.

### 9.5 부분 관측 및 예측 정보

- 현재 전체 상태 공개 구조에서 향후 부분 관측 구조로 전환 가능해야 한다.
- 수요 예측값이나 교통 예측값을 상태에 추가할 수 있어야 한다.

### 9.6 충전 및 운영 제약

- EV 충전, 운행 제한 시간, 지역 우선순위, 차량 타입 차이 등 운영 현실 제약을 추가할 수 있어야 한다.

### 9.7 다목적 최적화

- 단일 reward가 아닌 다중 목적 비교 및 Pareto 분석이 가능하도록 KPI를 독립적으로 저장해야 한다.


## 10. 비기능 요구사항

- 동일한 seed에 대해 동일한 결과를 재현할 수 있어야 한다.
- 로그와 결과는 디버깅 가능한 수준으로 충분히 남겨야 한다.
- 핵심 설정은 하드코딩하지 않고 외부 파일로 관리해야 한다.
- 차량 및 요청 수가 소규모인 MVP 환경에서는 빠르게 반복 실행 가능해야 한다.
- 실험 결과를 후속 분석에 활용할 수 있도록 CSV 또는 JSON 형태로 저장 가능해야 한다.


## 11. MVP 제외 범위

다음 항목은 초기 버전 범위에서 제외하되, 추후 확장 후보로 관리한다.

- 실제 지도 API 연동
- 실시간 외부 교통 데이터 연동
- 복수 승객 합승 최적화
- 복잡한 차량 충전/배터리 모델
- 다중 디포 운영
- 온라인 학습 및 실시간 운영 반영
- 대규모 도시 단위 시뮬레이션


## 12. 권장 MVP 구현 순서

1. 공간 모델, 차량 객체, 요청 객체 정의
2. step 기반 환경과 상태 전이 로직 구현
3. baseline 정책 2개 이상 구현
4. reward 및 KPI 계산 모듈 구현
5. scenario/config 기반 실행 구조 추가
6. 결과 저장 및 시각화 추가
7. RL agent 연결용 인터페이스 정리


## 13. 향후 고도화 로드맵

### Phase 1. MVP

- 차량 2대
- 단일 승객 요청 처리
- 합승 없음
- grid 기반 이동
- baseline 정책 비교
- 기본 KPI 및 시각화

### Phase 2. RL 학습 연결

- Gym 스타일 인터페이스 정리
- action mask 적용
- reward weight 실험
- 학습/검증/테스트 시나리오 분리

### Phase 3. 현실 제약 반영

- 차량 수 증가
- 수요 랜덤 생성
- 지역별 수요 hotspot
- 제한 시간 및 취소 규칙 정교화

### Phase 4. 고도화

- event-driven SMDP 전환
- 합승 및 경로 삽입 최적화
- 실제 도로 그래프 반영
- 수요 예측 및 모델 기반 planning 연계


## 14. 설계 시 우선 결정이 필요한 항목

구현 전 아래 항목은 우선 합의가 필요하다.

- 공간 모델을 `grid`로 시작할지 `graph`로 시작할지
- 시간 모델을 `step-based`로 시작할지 `event-driven`으로 시작할지
- 요청 입력을 `고정 시나리오` 중심으로 할지 `랜덤 생성` 중심으로 할지
- 합승을 MVP에서 제외할지 일부 제한적으로 포함할지
- RL 학습 프레임워크 인터페이스를 Gym 스타일로 맞출지 여부


## 15. 결론

초기 버전 시뮬레이터는 단순한 구조로 시작하되, 정책 비교와 RL 연결에 필요한 핵심 구성요소를 빠짐없이 포함해야 한다.
특히 `상태`, `행동`, `보상`, `제약조건`, `KPI`, `시나리오 재현성`을 초기에 명확히 정의해야 이후 고도화 비용을 줄일 수 있다.

권장되는 초기 구현 방향은 다음과 같다.

- `2대 차량 + 단일 승객 요청 + grid 공간 + step 기반 환경`
- `baseline 정책 비교 가능`
- `reward와 KPI 분리`
- `config/scenario 기반 재현 가능한 실험 구조`
- `향후 SMDP, 합승, 실제 도로망으로 확장 가능한 모듈화 설계`


## 16. 개발 설계안

본 절은 앞선 요구사항을 실제 구현 가능한 수준의 설계로 구체화한 내용이다.
초기 구현은 `Python 기반의 step-driven simulator`를 기준으로 설계한다.

### 16.1 설계 원칙

- 환경(`Environment`)과 정책(`Policy`)을 분리한다.
- 상태 원본 객체와 RL 입력용 상태 벡터를 분리한다.
- reward 계산과 KPI 집계를 분리한다.
- 시나리오 생성과 환경 실행을 분리한다.
- MVP 구현 시 단순성을 우선하되, 확장 포인트는 인터페이스로 남긴다.

### 16.2 권장 기술 스택

- 언어: Python 3.10+
- 설정 관리: YAML 또는 JSON
- 수치 계산: `numpy`
- 데이터 저장: `json`, `csv`
- 시각화: `matplotlib`
- RL 연동: 추후 `gymnasium` 스타일 인터페이스 호환


## 17. 디렉터리 및 패키지 구조 설계

권장 디렉터리 구조는 다음과 같다.

```text
RL_simulator/
  plan.md
  configs/
    base.yaml
    reward_wait_time.yaml
    reward_distance.yaml
  scenarios/
    scenario_simple_01.yaml
    scenario_simple_02.yaml
  simulator/
    __init__.py
    env.py
    clock.py
    state_encoder.py
    action_space.py
    entities/
      __init__.py
      vehicle.py
      request.py
      position.py
    models/
      __init__.py
      config.py
      metrics.py
      events.py
      result.py
    policies/
      __init__.py
      base.py
      random_policy.py
      nearest_vehicle_policy.py
      eta_policy.py
    rewards/
      __init__.py
      base.py
      weighted_reward.py
    constraints/
      __init__.py
      base.py
      capacity.py
      availability.py
      wait_time.py
      action_mask.py
    scenarios/
      __init__.py
      loader.py
      generator.py
    evaluation/
      __init__.py
      runner.py
      comparator.py
    visualization/
      __init__.py
      plot_paths.py
      plot_timeline.py
      plot_metrics.py
  scripts/
    run_simulation.py
    compare_policies.py
    generate_scenario.py
  results/
    .gitkeep
```

설계 의도는 다음과 같다.

- `simulator/env.py`는 전체 환경 실행의 진입점 역할을 담당한다.
- `entities/`는 차량, 요청 등 도메인 객체를 정의한다.
- `constraints/`는 불가능한 행동 판단과 mask 생성을 담당한다.
- `rewards/`는 학습용 reward 계산만 담당한다.
- `evaluation/`은 KPI 계산, 다회 실행, 정책 비교 리포트를 담당한다.
- `scripts/`는 실험 실행용 CLI 엔트리 포인트를 담당한다.


## 18. 핵심 도메인 모델 설계

### 18.1 Position

역할:

- 위치 정보를 표현한다.
- grid 기반 거리 계산의 입력 단위로 사용한다.

예상 필드:

- `x: int`
- `y: int`

예상 메서드:

- `distance_to(other) -> float`
- `step_toward(other, speed) -> Position`

### 18.2 Vehicle

역할:

- 차량 상태와 요청 처리 상태를 보관한다.
- step마다 이동 및 승하차 처리를 수행한다.

예상 필드:

- `vehicle_id: str`
- `position: Position`
- `status: VehicleStatus`
- `capacity: int`
- `current_load: int`
- `speed: float`
- `available_until: int`
- `assigned_request_id: str | None`
- `target_position: Position | None`
- `distance_travelled: float`
- `idle_time: int`
- `service_time: int`

예상 메서드:

- `assign_request(request)`
- `set_reposition_target(position)`
- `move_one_step()`
- `pickup(request)`
- `dropoff(request)`
- `is_idle() -> bool`
- `is_available(current_time) -> bool`

MVP 단순화:

- 차량은 동시에 하나의 요청만 처리한다.
- 내부 route list 대신 단일 target 기반으로 이동한다.

### 18.3 Request

역할:

- 승객 요청의 lifecycle을 표현한다.

예상 필드:

- `request_id: str`
- `created_at: int`
- `origin: Position`
- `destination: Position`
- `max_wait_time: int`
- `status: RequestStatus`
- `assigned_vehicle_id: str | None`
- `pickup_time: int | None`
- `dropoff_time: int | None`
- `cancelled_at: int | None`

예상 메서드:

- `assign(vehicle_id, current_time)`
- `mark_onboard(current_time)`
- `mark_completed(current_time)`
- `mark_cancelled(current_time)`
- `waiting_time(current_time) -> int`

### 18.4 Config

역할:

- 시뮬레이터 실행 파라미터를 단일 객체로 관리한다.

예상 필드:

- `grid_width`
- `grid_height`
- `max_time`
- `num_vehicles`
- `vehicle_capacity`
- `vehicle_speed`
- `vehicle_available_time`
- `reward_weights`
- `random_seed`
- `allow_reposition`

### 18.5 EpisodeResult

역할:

- 단일 episode 실행 결과를 표준 형식으로 저장한다.

예상 필드:

- `policy_name`
- `scenario_name`
- `total_reward`
- `metrics`
- `vehicle_logs`
- `request_logs`
- `step_logs`


## 19. 상태 표현 설계

### 19.1 내부 상태와 학습 상태 분리

환경 내부에서는 객체 기반 상태를 유지한다.

- 차량 목록
- 요청 목록
- 현재 시각
- 대기 요청 큐
- 완료/취소 요청 목록

RL 정책 입력 시에는 별도의 `StateEncoder`가 이를 고정 길이 벡터 또는 dict로 변환한다.

이 분리가 필요한 이유는 다음과 같다.

- 디버깅과 시각화는 객체 기반 상태가 유리하다.
- RL 모델 입력은 고정 길이 텐서 형태가 유리하다.
- 이후 baseline 정책과 RL 정책이 같은 원본 상태를 공유하되 표현만 다르게 할 수 있다.

### 19.2 상태 인코딩 예시

MVP 기준 고정 길이 상태 벡터 구성 예시는 다음과 같다.

```text
[current_time,
 vehicle_1_x, vehicle_1_y, vehicle_1_status, vehicle_1_remaining_time,
 vehicle_2_x, vehicle_2_y, vehicle_2_status, vehicle_2_remaining_time,
 waiting_request_count,
 req_1_origin_x, req_1_origin_y, req_1_dest_x, req_1_dest_y, req_1_wait,
 req_2_origin_x, req_2_origin_y, req_2_dest_x, req_2_dest_y, req_2_wait,
 ... padding ...]
```

설계 원칙:

- 요청 수 상한 `max_visible_requests`를 둔다.
- 부족한 요청 슬롯은 0 또는 sentinel 값으로 padding 한다.
- 차량 상태와 요청 상태는 정수 enum으로 인코딩한다.


## 20. 행동 공간 설계

### 20.1 MVP 행동 타입

행동은 다음 3개 타입으로 제한한다.

- `ASSIGN`
- `REPOSITION`
- `WAIT`

표현 예시:

```text
Action(
  action_type="ASSIGN",
  vehicle_id="V1",
  request_id="R3",
  target_position=None
)
```

### 20.2 ActionSpace 모듈 역할

`ActionSpace`는 다음 역할을 가진다.

- 현재 상태에서 가능한 행동 목록 생성
- 행동을 정수 인덱스로 매핑
- RL 학습용 action mask 생성

예상 메서드:

- `build_actions(state) -> list[Action]`
- `build_mask(state) -> np.ndarray`
- `decode(action_index) -> Action`

### 20.3 행동 제약 규칙

MVP에서는 아래 조건을 만족하지 못하면 `ASSIGN` 불가로 처리한다.

- 차량이 idle 상태일 것
- 차량 가용 시간이 남아 있을 것
- 요청이 waiting 상태일 것
- 요청 최대 대기 시간 초과 전일 것
- 차량 capacity 제약을 만족할 것


## 21. Environment 설계

### 21.1 핵심 책임

`MobilityEnv`는 다음 책임을 가진다.

- 시나리오 초기화
- 현재 상태 반환
- 유효 행동 목록 계산
- 행동 적용
- 차량 이동 처리
- 요청 상태 업데이트
- reward 계산
- KPI 누적
- 종료 판정

### 21.2 내부 속성

예상 필드:

- `config`
- `current_time`
- `vehicles`
- `requests`
- `pending_requests`
- `active_requests`
- `completed_requests`
- `cancelled_requests`
- `reward_calculator`
- `constraint_checker`
- `metrics_collector`
- `logger`
- `done`

### 21.3 step 처리 순서

MVP의 `step(action)` 처리 순서는 다음과 같이 고정한다.

1. 현재 시각에 생성될 요청을 활성화한다.
2. 입력 행동이 유효한지 검사한다.
3. 행동을 적용한다.
4. 각 차량을 1 step 이동시킨다.
5. pickup/dropoff 도착 여부를 처리한다.
6. 최대 대기 시간 초과 요청을 취소한다.
7. step reward를 계산한다.
8. KPI 및 로그를 기록한다.
9. 종료 여부를 판정한다.
10. 다음 상태를 반환한다.

이 순서를 명시하는 이유는 다음과 같다.

- 정책 비교 시 상태 전이 기준을 고정하기 위함
- reward 산정 시점 혼선을 방지하기 위함
- 향후 event-driven 구조로 변환할 때 이벤트 순서를 명확히 하기 위함

### 21.4 반환 형식

Gym 스타일을 고려한 기본 반환 형식:

- `next_state`
- `reward`
- `done`
- `info`

`info`에는 다음 정보를 넣는 것이 바람직하다.

- `valid_action_count`
- `new_requests`
- `completed_requests`
- `cancelled_requests`
- `step_metrics`


## 22. Reward 및 KPI 설계

### 22.1 RewardCalculator

역할:

- 매 step마다 학습용 reward를 계산한다.

입력 예시:

- 현재 시각
- 활성 요청 목록
- 완료 요청 수
- 취소 요청 수
- step 이동 거리
- 제약 위반 여부

출력:

- `step_reward`
- 항목별 reward breakdown

### 22.2 MetricsCollector

역할:

- reward와 독립적으로 평가용 KPI를 누적 계산한다.

누적 KPI 예시:

- 평균 대기 시간
- 평균 탑승 완료 시간
- 총 이동 거리
- 공차 거리
- 차량별 유휴 시간
- 요청 성공률

설계 포인트:

- 학습 중에는 reward를 사용하더라도, 실험 보고서는 KPI 기준으로 비교한다.
- reward weight가 바뀌어도 동일 KPI로 성능 비교가 가능해야 한다.


## 23. 제약조건 모듈 설계

제약조건은 환경 내부에 하드코딩하지 않고 별도 모듈로 분리한다.

### 23.1 ConstraintChecker 역할

- 행동 가능 여부 판단
- 요청 취소 조건 판단
- 차량 운행 가능 여부 판단

### 23.2 세부 제약 모듈

- `capacity.py`
  - 차량 적재 가능 여부 검사
- `availability.py`
  - 차량 가용 시간 검사
- `wait_time.py`
  - 요청 최대 대기 시간 초과 여부 검사
- `action_mask.py`
  - 정책 입력용 valid action mask 생성

설계 장점:

- 제약 추가 시 env 수정 범위를 최소화할 수 있다.
- 추후 EV 배터리, 지역 제한, 근무 제약 등을 쉽게 추가할 수 있다.


## 24. 정책 인터페이스 설계

### 24.1 BasePolicy

모든 정책은 동일한 인터페이스를 구현한다.

예상 메서드:

- `reset()`
- `select_action(state, valid_actions, info=None) -> Action`
- `get_name() -> str`

### 24.2 Baseline 정책 설계

`RandomPolicy`

- 가능한 행동 중 하나를 무작위 선택

`NearestVehiclePolicy`

- 요청별로 가장 가까운 idle 차량을 배정

`ETAPolicy`

- 픽업 예상 시간이 가장 짧은 차량을 배정

`IdleHoldPolicy`

- idle 차량은 재배치 없이 대기

### 24.3 RL 정책 연동

초기 버전에서는 RL 알고리즘 자체보다 연결 인터페이스를 우선 설계한다.

연동 요구사항:

- 환경이 정수 action index를 받을 수 있어야 한다.
- valid action mask를 제공해야 한다.
- state encoder를 교체 가능해야 한다.
- 학습 loop는 외부 trainer가 담당하도록 분리한다.


## 25. 시나리오 및 설정 스키마 설계

### 25.1 Config 파일 예시

```yaml
simulation:
  grid_width: 10
  grid_height: 10
  max_time: 100
  time_step: 1
  random_seed: 42

vehicles:
  count: 2
  capacity: 1
  speed: 1
  available_time: 100
  initial_positions:
    - [0, 0]
    - [9, 9]

reward:
  wait_time: 1.0
  travel_distance: 0.2
  completed_request: 3.0
  violation: 5.0
  cancelled_request: 4.0

options:
  allow_reposition: true
  max_visible_requests: 5
```

### 25.2 Scenario 파일 예시

```yaml
scenario_name: simple_case_01
requests:
  - request_id: R1
    created_at: 0
    origin: [1, 1]
    destination: [4, 5]
    max_wait_time: 10
  - request_id: R2
    created_at: 3
    origin: [8, 8]
    destination: [6, 2]
    max_wait_time: 8
```

설계 원칙:

- config는 시스템 파라미터
- scenario는 수요 데이터
- 두 파일을 분리하여 동일 환경에서 다양한 시나리오 실험이 가능하도록 한다.


## 26. 로그 및 결과 저장 설계

### 26.1 Step 로그

매 step마다 최소한 다음 정보를 저장한다.

- current_time
- action
- vehicle states
- request states
- reward
- reward breakdown

### 26.2 Episode 결과

파일 저장 예시:

- `results/{run_id}/summary.json`
- `results/{run_id}/step_log.json`
- `results/{run_id}/vehicle_log.csv`
- `results/{run_id}/request_log.csv`

### 26.3 비교 결과

정책 비교 실행 후 다음 파일 생성을 권장한다.

- `comparison_metrics.csv`
- `comparison_summary.json`
- `comparison_plot.png`


## 27. 실행 흐름 설계

전체 실행 흐름은 다음과 같다.

1. config 파일 로드
2. scenario 파일 로드
3. env 초기화
4. policy 초기화
5. episode loop 수행
6. 결과 저장
7. 필요 시 여러 policy 반복 실행
8. 비교 리포트 생성

의사코드 예시:

```python
config = load_config(config_path)
scenario = load_scenario(scenario_path)
env = MobilityEnv(config=config, scenario=scenario)
policy = NearestVehiclePolicy()

state = env.reset()
policy.reset()

while not env.is_done():
    valid_actions = env.get_valid_actions()
    action = policy.select_action(state, valid_actions)
    state, reward, done, info = env.step(action)

result = env.get_result()
save_result(result)
```


## 28. 클래스 관계 설계

핵심 객체 관계는 다음과 같다.

```text
ScenarioLoader -> Scenario
ConfigLoader   -> Config

Config + Scenario -> MobilityEnv
MobilityEnv -> Vehicle[]
MobilityEnv -> Request[]
MobilityEnv -> RewardCalculator
MobilityEnv -> ConstraintChecker
MobilityEnv -> MetricsCollector
MobilityEnv -> StateEncoder
MobilityEnv <-> Policy

Runner -> MobilityEnv + Policy
Comparator -> multiple EpisodeResult
Visualizer -> EpisodeResult / ComparisonResult
```

설계 의도:

- 실행 책임은 `Runner`
- 상태 전이 책임은 `MobilityEnv`
- 의사결정 책임은 `Policy`
- 결과 비교 책임은 `Comparator`


## 29. 1차 구현 마일스톤

### Milestone 1. 코어 환경 구축

- Position, Vehicle, Request 클래스 구현
- Config/Scenario 로더 구현
- MobilityEnv reset/step 구현
- 단일 scenario 실행 가능 상태 도달

완료 기준:

- 고정 요청 2건 이상을 넣었을 때 차량 이동과 요청 완료가 정상 동작

### Milestone 2. baseline 정책 및 KPI

- RandomPolicy 구현
- NearestVehiclePolicy 구현
- RewardCalculator 구현
- MetricsCollector 구현

완료 기준:

- 동일 scenario에서 정책별 KPI 비교 가능

### Milestone 3. action mask 및 RL 연동 준비

- ActionSpace 구현
- valid action mask 구현
- state encoder 구현
- Gym 스타일 반환 형식 정리

완료 기준:

- 외부 RL trainer가 붙을 수 있는 최소 인터페이스 확보

### Milestone 4. 결과 분석 및 시각화

- step log 저장
- 차량 경로 시각화
- 정책 비교 그래프 생성

완료 기준:

- 단일 실행 및 비교 실행 결과를 파일로 저장하고 시각적으로 확인 가능


## 30. 테스트 설계

초기 버전에서도 최소 테스트는 반드시 필요하다.

### 30.1 단위 테스트 대상

- 거리 계산이 올바른지
- 차량 이동이 목표 방향으로 수행되는지
- 요청 상태 전이가 올바른지
- 제약조건 위반 행동이 차단되는지
- reward 계산이 설정값대로 동작하는지

### 30.2 통합 테스트 대상

- scenario 로드 후 전체 episode가 정상 종료되는지
- baseline 정책 실행 시 결과 파일이 생성되는지
- 동일 seed에서 동일 결과가 재현되는지

### 30.3 예외 테스트 대상

- 요청이 0건인 scenario
- 모든 차량이 초기부터 unavailable인 경우
- 최대 대기 시간을 즉시 초과하는 요청
- 유효 행동이 없는 step


## 31. 향후 코드 구현 우선순위 제안

실제 개발은 아래 순서로 진행하는 것을 권장한다.

1. `entities`, `models`, `scenarios` 구현
2. `env.py`와 기본 상태 전이 구현
3. `constraints`와 `rewards` 구현
4. `policies` 구현
5. `evaluation`과 `results` 저장 구현
6. `visualization` 구현
7. `gymnasium` 호환 인터페이스 정리


## 32. 최종 권장 구현 방향

초기 개발은 다음 기준으로 고정하는 것이 가장 안정적이다.

- 공간 모델: `2D grid`
- 시간 모델: `step-based`
- 차량 수: `2`
- 수용 인원: `1`
- 요청 처리: `합승 없음`
- 정책 비교: `Random + Nearest + ETA`
- 상태 표현: `객체 상태 + 별도 인코더`
- 실행 구조: `config + scenario + runner`

이 설계로 시작하면 구현 난이도를 통제하면서도, 이후 RL 학습 연결과 고도화가 비교적 자연스럽게 이어질 수 있다.

