"""Default Local ITS speed settings derived from bundled recent traffic averages."""

from __future__ import annotations

DEFAULT_LOCAL_ITS_SPEED_PROFILE_SOURCE = (
    "TESTAM/etc/dongjakITS_2024_all_0101_1231_시분별_평균.csv"
)

# 2024 bundled ITS averages grouped by hour from the latest available
# `dongjakITS_2024_all_0101_1231_시분별_평균.csv` dataset.
DEFAULT_LOCAL_ITS_HOURLY_SPEED_KPH: list[float] = [
    28.8,
    29.3,
    29.7,
    29.8,
    29.8,
    29.1,
    26.3,
    24.5,
    23.4,
    23.3,
    23.0,
    23.0,
    23.2,
    22.9,
    22.6,
    22.4,
    22.0,
    21.6,
    21.7,
    22.9,
    24.7,
    25.5,
    26.2,
    27.3,
]

DEFAULT_LOCAL_ITS_AVERAGE_SPEED_KPH = 25.1
