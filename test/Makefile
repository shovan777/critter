# ============================================================================
#  Critter safety-guideline test suite
#
#  THREE UNITS, THREE CARDS. Each unit ships as one runnable program, so each
#  is tested and scored as one thing:
#
#      unit 1  I/O       io_unit.c, run and interrupted, output audited
#      unit 2  memory    critter_mem.c, driven by log files
#      unit 3  compute   bin/critter, driven through its documented CLI
#
#  sensor_sim.c is LINKED so unit 1 can run -- io_unit.c cannot execute
#  without an implementation of sensor.h -- but is NOT audited or scored. It
#  sits behind the sensor.h hardware boundary and its own README designates it
#  the to-be-replaced part. Built, not reviewed.
#
#  Expected layout:
#
#      critter/
#        compute-intensive/   main.c mpc.c perf.c thermal.c  (or src/)
#                             include/critter.h  Makefile -> bin/critter
#        io-subsystem/        io_unit.c  sensor.h  sensor_sim.c
#        memory/src/          critter_mem.c
#        test/                <this directory>
#
#  Targets
#      make run        build, run all three layers, print the scorecard   <-- start here
#      make results    figures, tables, JSON and the PDF into results/
#      make runtime    the unit-2 in-process test binary only
#      make static     structural rules and compile probes only
#      make dynamic    integration, end-to-end and black-box checks (~20 s)
#      make verdicts   raw CT| lines, unscored
#      make report     the PDF only, from the latest run
#      make paths      show the resolved source paths and exit
#      make clean / make distclean
#
#  NOTE ON -std: the suite is built -std=gnu11, not -std=c11, because two of
#  the units do not compile under strict C11 -- io_unit.c fails outright on
#  CLOCK_REALTIME and mpc.c implicitly declares posix_memalign. Unit 3's own
#  README goes further and instructs against -std=c11 outright, for FMA
#  contraction. All of that is recorded as findings (DCL31-C, P10 Rule 10)
#  rather than absorbed into these flags. Do not "fix" it here.
# ============================================================================

U1     ?= ../io-subsystem
U2     ?= ../memory/src
U3     ?= ../compute-intensive
U3_INC ?= $(U3)/include

export U1 U2 U3 U3_INC

CC     ?= gcc
CFLAGS ?= -O1 -g -Wall -Wextra -std=gnu11
LDLIBS ?= -lm
PY     ?= python3

BUILD    := build
VERDICTS := $(BUILD)/verdicts.txt

# Unit 2 is the only unit tested in process: it is a single translation unit
# whose functions are all static, so the source is #included with its entry
# point renamed. Units 1 and 3 are tested as programs.
BINS := $(BUILD)/test_critter_mem

# The compute unit's README places sources under src/; the repository has them
# at the top level. Accept either.
U3_SRC := $(if $(wildcard $(U3)/src/mpc.c),$(U3)/src,$(U3))

.PHONY: all run runtime static dynamic verdicts results report paths \
        check-sources clean distclean help

all: $(BINS)

$(BUILD):
	@mkdir -p $(BUILD)

REQUIRED := $(U1)/io_unit.c $(U1)/sensor_sim.c $(U1)/sensor.h \
            $(U2)/critter_mem.c \
            $(U3_SRC)/mpc.c $(U3_SRC)/thermal.c $(U3_SRC)/perf.c \
            $(U3_SRC)/main.c $(U3_INC)/critter.h

check-sources:
	@missing=""; for f in $(REQUIRED); do \
	   [ -f "$$f" ] || missing="$$missing $$f"; done; \
	 if [ -n "$$missing" ]; then \
	   echo "ERROR: cannot find these sources:"; \
	   for f in $$missing; do echo "  $$f"; done; \
	   echo ""; echo "Resolved search paths:"; \
	   echo "  U1     = $(U1)"; echo "  U2     = $(U2)"; \
	   echo "  U3     = $(U3)  (sources in $(U3_SRC))"; \
	   echo "  U3_INC = $(U3_INC)"; \
	   echo ""; echo "Override on the command line, e.g."; \
	   echo "  make U2=../memory/src run"; exit 1; fi

paths:
	@echo "U1     = $(U1)         (io_unit.c, sensor_sim.c, sensor.h)"
	@echo "U2     = $(U2)         (critter_mem.c)"
	@echo "U3     = $(U3)         (built via its own Makefile -> bin/critter)"
	@echo "U3_SRC = $(U3_SRC)         (mpc.c, thermal.c, perf.c, main.c)"
	@echo "U3_INC = $(U3_INC)         (critter.h)"

$(BUILD)/test_critter_mem: test_critter_mem.c ct.h $(U2)/critter_mem.c | $(BUILD)
	$(CC) $(CFLAGS) -I$(U2) -o $@ $< $(LDLIBS)

runtime: check-sources $(BINS)
	@for b in $(BINS); do ./$$b; done

static: check-sources | $(BUILD)
	@$(PY) check_static.py

dynamic: check-sources | $(BUILD)
	@$(PY) check_dynamic.py

$(VERDICTS): $(BINS) check_static.py check_dynamic.py
	@mkdir -p $(BUILD)
	@: > $@
	@for b in $(BINS); do ./$$b >> $@; done
	@$(PY) check_static.py >> $@
	@$(PY) check_dynamic.py >> $@

verdicts: check-sources $(VERDICTS)
	@cat $(VERDICTS)

run: check-sources $(VERDICTS)
	@$(PY) scorecard.py $(VERDICTS)

results: check-sources $(VERDICTS)
	@$(PY) make_results.py

report: $(VERDICTS)
	@$(PY) make_report.py

clean:
	rm -rf $(BUILD) faults

distclean: clean
	rm -rf results

help:
	@sed -n '2,40p' Makefile