#!/usr/bin/env python3
"""
Test-data generator for the Critter memory-focused unit exemplar.

NOTE: This script is a TEST HARNESS, not part of the unit under review.
It exists only to produce input data. Do not include it in the safety
evaluation of critter_mem.c.

Output format (one reading per line, no header):
    MM/DD/YYYY HH:MM:SS,<temperature_celsius>
e.g.
    07/14/2024 15:22:41,26.118

Temperature model (synthetic machine-room profile):
    baseline 22 C
  + seasonal swing  +/-4 C  (peaks late June, troughs late December)
  + daily swing     +/-2 C  (peaks early afternoon)
  + sensor noise    +/-1 C  (uniform)
  => legitimate readings land roughly in [15 C, 29 C]

Outliers are then planted at a known, exact count so the C program's
reported removal count can be verified against ground truth.

Usage:
    python3 generate_temps.py [num_readings] [num_outliers] [output_file]
"""

import math
import random
import sys
from datetime import datetime, timedelta

# ---- Configuration --------------------------------------------------------
DEFAULT_READINGS = 1_000_000
DEFAULT_OUTLIERS = 40
DEFAULT_OUTFILE = "sample_temps.txt"

START = datetime(2023, 1, 1, 0, 0, 0)
END = datetime(2025, 12, 31, 23, 59, 59)

BASELINE_C = 22.0
SEASONAL_AMPLITUDE_C = 4.0
DAILY_AMPLITUDE_C = 2.0
NOISE_AMPLITUDE_C = 1.0

# Gross outlier values: sensor faults / disconnected probes.
# All are far outside the legitimate [15, 29] band, so a 3-sigma
# z-score filter is guaranteed to catch every one of them.
OUTLIER_VALUES = [85.0, 92.5, 97.3, 78.9, 88.1, -40.0, -28.5, -35.2, -31.7, -45.0]


def synthetic_temperature(when: datetime) -> float:
    """Return a plausible machine-room temperature for a given moment."""
    day_of_year = when.timetuple().tm_yday
    # Shift by 81 days so the seasonal peak falls near the summer solstice.
    seasonal = SEASONAL_AMPLITUDE_C * math.sin(2 * math.pi * (day_of_year - 81) / 365.0)

    hours = when.hour + when.minute / 60.0 + when.second / 3600.0
    # Shift by 6 hours so the daily peak falls near midday.
    daily = DAILY_AMPLITUDE_C * math.sin(2 * math.pi * (hours - 6) / 24.0)

    noise = random.uniform(-NOISE_AMPLITUDE_C, NOISE_AMPLITUDE_C)
    return BASELINE_C + seasonal + daily + noise


def random_timestamp(span_seconds: int) -> datetime:
    """Return a uniformly random moment inside the configured date range."""
    return START + timedelta(seconds=random.randint(0, span_seconds))


def main() -> None:
    num_readings = int(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_READINGS
    num_outliers = int(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_OUTLIERS
    outfile = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_OUTFILE

    random.seed(20260911)  # fixed seed => reproducible data set
    span_seconds = int((END - START).total_seconds())

    # 1. Build the legitimate readings.
    records = []
    for _ in range(num_readings - num_outliers):
        when = random_timestamp(span_seconds)
        records.append((when, synthetic_temperature(when)))

    # 2. Plant exactly num_outliers gross faults.
    planted = []
    for i in range(num_outliers):
        when = random_timestamp(span_seconds)
        value = OUTLIER_VALUES[i % len(OUTLIER_VALUES)]
        records.append((when, value))
        planted.append((when, value))

    # 3. Shuffle so the file is in no chronological order, as requested.
    random.shuffle(records)

    # 4. Write it out.
    with open(outfile, "w") as f:
        for when, temp in records:
            f.write("%s,%.3f\n" % (when.strftime("%m/%d/%Y %H:%M:%S"), temp))

    print("Wrote %d readings to %s" % (len(records), outfile))
    print("Planted %d outliers (ground truth for verification):" % num_outliers)
    for when, temp in sorted(planted):
        print("    %s,%.3f" % (when.strftime("%m/%d/%Y %H:%M:%S"), temp))


if __name__ == "__main__":
    main()
