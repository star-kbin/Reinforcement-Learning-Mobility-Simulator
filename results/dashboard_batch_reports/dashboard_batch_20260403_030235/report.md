# Dashboard Batch Experiment Report

- 생성 시각: `2026-04-03T03:02:35+00:00`
- 총 case 수: `1`
- 총 실행 수: `1`
- 정책: `nearest`

## 정책별 요약

| policy | run_count | avg_total_reward | avg_success_rate | best_case_id |
| --- | --- | --- | --- | --- |
| nearest | 1 | -1.002 | 1.0 | case_001__vehicle_count-1__window_start-2026-04-03_08_00_00__request_count-1__radius-500.0__max_wait_time-300__seed-101 |

## 상위 실행 결과

| case_id | policy | total_reward | success_rate | completed_requests | cancelled_requests |
| --- | --- | --- | --- | --- | --- |
| case_001__vehicle_count-1__window_start-2026-04-03_08_00_00__request_count-1__radius-500.0__max_wait_time-300__seed-101 | nearest | -1.002 | 1.0 | 1 | 0 |

## 배치 설정

```json
{
  "policies": [
    "nearest"
  ],
  "fixed_request_set": false,
  "save_run_payloads": false,
  "axes": {
    "vehicle_count": [
      1
    ],
    "window_start": [
      "2026-04-03 08:00:00"
    ],
    "request_count": [
      1
    ],
    "radius": [
      500.0
    ],
    "max_wait_time": [
      300
    ],
    "seed": [
      101
    ]
  }
}
```

## 출력 파일

- `run_results.csv`
- `policy_summary.csv`
- `report.md`
