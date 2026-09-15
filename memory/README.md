# Critter Memory-Focused Unit — Build and Run Guide

Terminal reference for the memory-focused unit exemplar on a Raspberry Pi 5 (8 GB).
Everything here runs from the shell; no IDE or GUI is needed.

The program reads a log of timestamped temperature readings, removes statistical
outliers, and writes two files: a cleaned log and a monthly summary.

---

## Files

| File | Purpose |
|---|---|
| `critter_mem.c` | The exemplar. The artifact under safety review. |
| `Makefile` | Build convenience. Gets the `-lm` ordering right. |
| `generate_temps.py` | Test-data generator. Not part of the reviewed unit. |
| `sample_temps.txt` | Pre-made 1,000,000-reading data set with 40 planted outliers. |

---

## One-time setup

Raspberry Pi OS ships with `python3` already. You need a compiler and `make`:

```bash
sudo apt update
sudo apt install build-essential
```

Optional, only if you want peak-memory measurements (`/usr/bin/time -v`):

```bash
sudo apt install time
```

Confirm the toolchain and that you are on a 64-bit userland:

```bash
gcc --version
make --version
python3 --version
getconf LONG_BIT        # expect 64 on a Pi 5
```

`getconf LONG_BIT` reports the **userland** bitness, which is what actually
matters here — `uname -m` can report a 64-bit kernel while userspace is 32-bit.
See "Why bitness matters" below.

---

## Quick start

```bash
make          # build
make run      # build if needed, then run against sample_temps.txt
```

That is the whole happy path. Everything below is detail.

---

## Building

### Using make

```bash
make          # build critter_mem
make clean    # delete the binary and generated output files
```

### Building by hand

```bash
gcc -O2 -Wall -Wextra -Wpedantic -std=c11 -o critter_mem critter_mem.c -lm
```

**`-lm` must come after `critter_mem.c`.** This is the single most common way to
fail at building this program. GNU `ld` resolves symbols left to right: if `-lm`
appears before the source file, the linker has not yet seen any unresolved
reference to `sqrt`, discards libm as unused, and then fails when the object
file needs it.

These all fail with `undefined reference to 'sqrt'`:

```bash
gcc -lm critter_mem.c -o critter_mem          # -lm too early
gcc critter_mem.c -o critter_mem              # -lm missing
gcc -O2 critter_mem.c -o critter_mem          # -O2 does not save you
```

### Compiler flags explained

| Flag | Meaning | Why it is used |
|---|---|---|
| `-O2` | Optimization level 2 | Standard release optimization. Also affects whether `sqrt` becomes a hardware instruction. |
| `-Wall` | Common warnings | Baseline diagnostics. |
| `-Wextra` | Additional warnings beyond `-Wall` | Catches unused parameters, sign-compare issues, and more. |
| `-Wpedantic` | Warn on non-standard constructs | Enforces strict ISO C conformance. |
| `-std=c11` | Use the C11 standard | The language version the source targets. |
| `-o critter_mem` | Output binary name | Without it you get `a.out`. |
| `-lm` | Link the math library | Required for `sqrt`. **Must be last.** |

The exemplar compiles with **zero warnings** under all four warning flags. If
you see any warning, something has been modified.

### Useful alternate builds

```bash
# Debug build: no optimization, debug symbols, for gdb or valgrind
gcc -O0 -g -Wall -Wextra -Wpedantic -std=c11 -o critter_mem critter_mem.c -lm

# Sanitizer build: catches buffer overruns and undefined behavior at runtime
gcc -O1 -g -fsanitize=address,undefined -std=c11 -o critter_mem critter_mem.c -lm
```

The sanitizer build is worth running for the safety evaluation — it exercises
the unvalidated-month-field path if you feed it malformed input. Expect it to be
several times slower and to use noticeably more memory.

---

## Running the program

```bash
./critter_mem <input_file>
```

**The program takes exactly one argument and has no command-line flags.** The
input file is required and positional. There is no `--help`, no `-v`, no output
path option; output file names are fixed at compile time.

Examples:

```bash
./critter_mem sample_temps.txt
./critter_mem /home/pi/logs/machine_room_2024.txt
./critter_mem temps_big.txt
```

### What it writes

| Output | Fixed name | Contents |
|---|---|---|
| Cleaned log | `critter_cleaned.txt` | Surviving readings, same format as input, input order preserved |
| Monthly summary | `critter_summary.csv` | 12 rows: month number, month name, count, mean, min, max |
| Console report | stdout | Before/after statistics, an itemized list of every discarded reading with its z-score, and the total removed |

Both files are written to the **current working directory** and overwritten
without warning. `cd` to where you want them before running.

Errors go to **stderr**, so you can separate them:

```bash
./critter_mem sample_temps.txt > report.txt 2> errors.txt
```

### Exit codes

| Code | Meaning |
|---|---|
| `0` | Success |
| `1` | Any failure: no argument given, file cannot be opened, file is empty, no lines matched the expected format, or memory allocation failed |

All failures return `1`; the program does not distinguish error classes by exit
code. The stderr message tells you which happened:

```
usage: ./critter_mem <input_file>
could not open <file>
<file> contains no readings
no readings in <file> matched the expected format
could not allocate memory for N readings
```

---

## Tuning constants

The program has no runtime flags. Its behavior is set by `#define`s near the
top of `critter_mem.c`:

| Constant | Default | Effect |
|---|---|---|
| `SIGMA_THRESHOLD` | `3.0` | Readings farther than this many standard deviations from the mean are discarded. Lower is more aggressive. |
| `MAX_LINE_LENGTH` | `128` | Input line buffer size in bytes, including the newline. |
| `NUM_MONTHS` | `12` | Summary bucket count. Do not change; the month-name table assumes 12. |
| `CLEANED_FILE` | `"critter_cleaned.txt"` | Cleaned output file name. |
| `SUMMARY_FILE` | `"critter_summary.csv"` | Summary output file name. |

**Edit the source and rebuild** to change these:

```bash
nano critter_mem.c        # change the #define, then:
make clean && make
```

Do **not** try to override them with `-D`:

```bash
gcc -DSIGMA_THRESHOLD=2.5 ...    # warning: "SIGMA_THRESHOLD" redefined
```

The `#define`s are unconditional, so `-D` produces a macro-redefinition warning.
Adding `#ifndef` guards to allow it would work, but it would also introduce
conditional compilation into the reviewed artifact, which is itself restricted
under NASA Power of Ten Rule 8. The exemplar is deliberately left alone.

---

## Generating test data

```bash
python3 generate_temps.py [num_readings] [num_outliers] [output_file]
```

All three arguments are positional and optional:

| Position | Argument | Default | Notes |
|---|---|---|---|
| 1 | `num_readings` | `1000000` | Total lines written, **including** the outliers |
| 2 | `num_outliers` | `40` | Exact count of gross faults planted |
| 3 | `output_file` | `sample_temps.txt` | Overwritten without warning |

Examples:

```bash
python3 generate_temps.py                                  # 1M readings, 40 outliers
python3 generate_temps.py 10000000                         # 10M readings, 40 outliers
python3 generate_temps.py 50000000 100 temps_huge.txt      # 50M readings, 100 outliers
```

### Generator behavior and defaults

| Property | Value |
|---|---|
| Date range | 2023-01-01 through 2025-12-31 |
| Record order | Shuffled — deliberately not chronological |
| Random seed | Fixed at `20260911`, so output is reproducible |
| Baseline temperature | 22 °C |
| Seasonal swing | ±4 °C, peaking near the summer solstice |
| Daily swing | ±2 °C, peaking early afternoon |
| Sensor noise | ±1 °C uniform |
| Legitimate range | roughly 15 °C to 29 °C |
| Outlier values | 78.9 to 97.3 °C and −45.0 to −28.5 °C |

The generator **prints every planted outlier** to stdout. That is your ground
truth for verifying the filter. With the default 40 you get 40 lines; if you
plant thousands, redirect it:

```bash
python3 generate_temps.py 10000000 500 big.txt > planted.txt
```

Because the seed is fixed, the same arguments always produce the same file. To
get different data, change the count or edit `random.seed()` in the script.

---

## Makefile reference

### Targets

| Target | What it does |
|---|---|
| `make` / `make all` | Build `critter_mem` |
| `make run` | Build if needed, generate `sample_temps.txt` if missing, then run |
| `make data` | Regenerate the 1,000,000-reading sample |
| `make big` | Generate a 10,000,000-reading data set as `temps_big.txt` |
| `make clean` | Remove the binary, `critter_cleaned.txt`, and `critter_summary.csv` |

### Overridable variables

Any variable can be overridden on the command line:

```bash
make big N_BIG=50000000              # 50M readings instead of 10M
make data N_OUTLIERS=200             # plant 200 outliers instead of 40
make CFLAGS="-O0 -g -std=c11"        # debug build
make CC=clang                        # build with clang instead of gcc
```

| Variable | Default |
|---|---|
| `CC` | `gcc` |
| `CFLAGS` | `-O2 -Wall -Wextra -Wpedantic -std=c11` |
| `LDLIBS` | `-lm` |
| `N_SAMPLE` | `1000000` |
| `N_BIG` | `10000000` |
| `N_OUTLIERS` | `40` |

Note that `make clean` does **not** delete `sample_temps.txt` or
`temps_big.txt`. Generating data is slow, so the data files are left alone
deliberately. Remove them by hand if you want:

```bash
rm -f sample_temps.txt temps_big.txt
```

---

## File formats

### Input

Plain text, one reading per line, no header, 24-hour clock:

```
MM/DD/YYYY HH:MM:SS,<temperature_celsius>
```

```
09/04/2025 15:02:57,23.846
01/28/2023 16:03:20,19.081
```

Readings do **not** need to be in chronological order. Lines that do not match
the format are skipped, and the count of skipped lines is reported.

### Cleaned output

Identical format to the input, so it can be fed straight back in:

```bash
./critter_mem sample_temps.txt
mv critter_cleaned.txt pass1.txt
./critter_mem pass1.txt              # second pass over already-cleaned data
```

### Summary output

```
month,month_name,reading_count,mean_temp_c,min_temp_c,max_temp_c
1,January,85191,18.444,15.080,21.929
2,February,77420,19.740,16.039,23.510
```

Months with no readings get a zero count and empty statistics fields. All twelve
months are always emitted.

---

## Choosing a data set size on an 8 GB Pi 5

The program holds the entire data set in RAM at roughly **80 bytes per
reading** — two 32-byte record buffers plus an 8-byte-per-reading sorted copy
for the median.

| Readings | Input file | Working set | Verdict |
|---|---|---|---|
| 1,000,000 | 26 MB | 78 MB | Trivial |
| 10,000,000 | 260 MB | ~800 MB | Comfortable |
| 25,000,000 | 650 MB | ~2.0 GB | Comfortable |
| 50,000,000 | 1.3 GB | ~4.0 GB | Fits, ~3 GB spare |
| 85,000,000 | 2.2 GB | ~6.8 GB | At the edge — OOM risk |

Check free memory before a large run:

```bash
free -h
```

Measure what a run actually used:

```bash
/usr/bin/time -v ./critter_mem temps_big.txt 2>&1 | grep "Maximum resident"
```

Watch it live from a second terminal:

```bash
watch -n1 'grep VmHWM /proc/$(pgrep -x critter_mem)/status'
```

If a run dies without printing anything, suspect the OOM killer:

```bash
sudo dmesg | grep -i "killed process"
```

Run headless (no desktop) for the largest data sets — the desktop alone can hold
several hundred megabytes that you could otherwise give to the program.

---

## Verifying the filter works

The generator plants an exact number of outliers, so correctness is checkable
rather than assumed:

```bash
python3 generate_temps.py 1000000 40 sample_temps.txt | tee planted.txt
./critter_mem sample_temps.txt | grep "Outliers found"
```

Expect `Outliers found and removed: 40`. Cross-check the line counts:

```bash
wc -l sample_temps.txt critter_cleaned.txt
```

The difference should equal the number of planted outliers. And confirm nothing
legitimate was discarded — every surviving reading should fall inside the
roughly 15–29 °C band:

```bash
cut -d, -f2 critter_cleaned.txt | sort -n | head -1    # coldest survivor
cut -d, -f2 critter_cleaned.txt | sort -n | tail -1    # warmest survivor
```

---

## Known limitations and gotchas

These are properties of a deliberately unoptimized, un-hardened exemplar, not
bugs to be fixed. They are documented because they matter for the safety
evaluation.

**Constant input discards everything and still reports success.** If every
reading in the file is identical, the standard deviation is zero, the z-score
becomes `0/0 = NaN`, `NaN <= 3.0` evaluates false, and **all** readings are
classified as outliers. Observed with a five-line file of identical values:

```
count   = 5
std dev = 0.000 C
Outliers found and removed: 5
count   = 0
std dev = -nan C
```

The program exits `0`. A silent total data loss with a success exit code is
worse than a crash, and it is the concrete manifestation of the CERT FLP03
divide-by-zero finding. A single-reading file behaves the same way.

**Output files are overwritten silently.** Both output names are fixed. Running
twice in the same directory destroys the first result.

**Lines longer than 127 characters are split.** `MAX_LINE_LENGTH` is 128
including the newline; a longer line is read in pieces, and the fragments
generally fail to parse and are counted as malformed.

**The month field is not range-checked.** A malformed line that parses as month
`13` or `0` indexes outside the summary array. Valid input never triggers it;
the `-fsanitize=address` build will catch it if you feed it deliberately bad
data.

**Outliers contaminate their own threshold.** The mean and standard deviation
are computed over the raw data, including the outliers. Forty faults in a
million readings shift the threshold by well under 1%, but heavy contamination
would degrade the filter.

**The cleaned file preserves input order.** Since the sample data is shuffled,
the cleaned output is shuffled too. It is not sorted chronologically.

---

## Troubleshooting

| Symptom | Cause and fix |
|---|---|
| `undefined reference to 'sqrt'` | `-lm` missing or placed before the source file. Put it last, or use `make`. |
| `gcc: command not found` | `sudo apt install build-essential` |
| `make: command not found` | `sudo apt install build-essential` |
| `/usr/bin/time: No such file` | `sudo apt install time`. The shell builtin `time` has no `-v`. |
| `usage: ./critter_mem <input_file>` | No argument given. Pass the input file path. |
| `could not open <file>` | Wrong path, or no read permission. Check with `ls -l`. |
| `<file> contains no readings` | The file is empty. |
| `no readings ... matched the expected format` | Format mismatch. Verify with `head -3 <file>` against the format above. |
| `Skipped N malformed line(s)` | Informational. Some lines did not parse; the rest were processed. |
| Killed with no output | Out of memory. Use a smaller data set, or run headless. Confirm with `sudo dmesg \| grep -i "killed process"`. |
| Reported outlier count does not match what was planted | Check you are running against the file you just generated. The generator overwrites its output. |
| `Permission denied` running `./critter_mem` | `chmod +x critter_mem` |

---

## Full worked example

```bash
# setup
sudo apt update && sudo apt install build-essential

# build
make

# generate 10 million readings with 40 known outliers, keeping ground truth
python3 generate_temps.py 10000000 40 temps_big.txt > planted.txt

# run, capturing the report
./critter_mem temps_big.txt | tee report.txt

# verify
grep "Outliers found" report.txt
wc -l temps_big.txt critter_cleaned.txt
head -13 critter_summary.csv

# clean up the binary and outputs (data files are kept)
make clean
```