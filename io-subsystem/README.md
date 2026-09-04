# Critter — I/O Unit (prototype)

This is the **I/O unit**, one of the three functional components of the Critter:

1. **I/O unit** *(this code)* — reads temperature values at a high rate.
2. Memory unit — optimizes the collected data, removes outliers, summarizes for offline analysis.
3. Compute unit — evaluates the data to predict HVAC behavior.

The I/O unit's job is to sample the sensor continuously and emit a clean,
timestamped stream of raw readings that the memory unit can consume.

## Files

|File|Purpose|
|-|-|
|`sensor.h`|The hardware-boundary interface: `sensor\_init / read\_raw / shutdown`.|
|`sensor\_sim.c`|Software simulation of the sensor — **the to-be-replaced part**.|
|`io\_unit.c`|The I/O unit proper: sampling loop, buffering, CSV output.|
|`Makefile`|Builds `io\_unit`.|

### The hardware boundary

Everything that touches the "physical" sensor lives behind `sensor.h`.
Right now that interface is implemented by `sensor\_sim.c` (a random walk
around room temperature, with occasional glitch readings). To move to real
hardware, replace `sensor\_sim.c` with a driver for the actual sensor that
implements the same three functions — nothing in `io\_unit.c` should change.

## Output format

CSV on the file given as the first argument, or `temperature\_raw.csv` by
default. One header line, then one row per sample:

```
seq,timestamp\_ms,raw\_temp\_cC
0,1717000000123,2201
1,1717000000133,2199
```

* `seq` — monotonic sample index.
* `timestamp\_ms` — wall-clock time of the read, in milliseconds.
* `raw\_temp\_cC` — temperature in hundredths of a degree Celsius (2201 = 22.01 C).

## Build and run

```
make
./io\_unit                 # writes temperature\_raw.csv
./io\_unit mydata.csv      # or choose the output file
```

Press `Ctrl-C` to stop; the unit flushes the buffer and exits cleanly.
Default sample rate is 100 Hz (see `SAMPLE\_PERIOD\_NS` in `io\_unit.c`).

### Reproducible input

The simulated sensor is seeded from a fixed constant by default, so the
`seq,raw\_temp\_cC` stream is identical on every run. Override the seed with
the `CRITTER\_SEED` environment variable to get a different (but still
repeatable) walk:

```
./io\_unit                       # default seed, repeatable
CRITTER\_SEED=999 ./io\_unit      # different repeatable stream
```

Note that `timestamp\_ms` comes from the wall clock and will differ every
run regardless of seed. For comparing two builds (e.g. a baseline vs a
hardened version), compare on the `seq` and `raw\_temp\_cC` columns only.

Target platform is the Raspberry Pi 5 (Raspberry Pi OS / aarch64 Linux).
It has been verified to build and run under Linux with GCC.

## Status

This is a **first-pass, get-it-working prototype**. It has intentionally
**not** been hardened against any coding standard (Power of 10, SEI CERT, etc.)

