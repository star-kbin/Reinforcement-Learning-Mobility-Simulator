# Dashboard Batch Experiment Report

- 생성 시각: `2026-04-03T04:42:26+00:00`
- 총 case 수: `1`
- 총 실행 수: `1`
- 정책: `eta`

## 정책별 요약

| policy | run_count | avg_total_reward | avg_success_rate | best_case_id |
| --- | --- | --- | --- | --- |
| eta | 1 | -7.01 | 1.0 | case_001__vehicle_count-1__window_start-2026-04-03_12_00_00__request_count-4__radius-500.0__max_wait_time-600__seed-1686903714 |

## 상위 실행 결과

| case_id | policy | total_reward | success_rate | completed_requests | cancelled_requests |
| --- | --- | --- | --- | --- | --- |
| case_001__vehicle_count-1__window_start-2026-04-03_12_00_00__request_count-4__radius-500.0__max_wait_time-600__seed-1686903714 | eta | -7.01 | 1.0 | 4 | 0 |

## 배치 설정

```json
{
  "policies": [
    "eta"
  ],
  "fixed_request_set": false,
  "save_run_payloads": false,
  "axes": {
    "vehicle_count": [
      1
    ],
    "window_start": [
      "2026-04-03 12:00:00"
    ],
    "request_count": [
      4
    ],
    "radius": [
      500.0
    ],
    "max_wait_time": [
      600
    ],
    "seed": [
      1686903714
    ]
  },
  "base_request_count": 8,
  "base_vehicle_count": 1
}
```

## 출력 파일

- `run_results.csv`
- `policy_summary.csv`
- `report.md`
