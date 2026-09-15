# Critter unit 3 — compute-bound MPC
CC      ?= gcc
STD      = -std=gnu11
WARN     = -Wall -Wextra -Wno-unused-parameter 
# -fanalyzer
OPT     ?= -O3 -ffast-math -fno-math-errno
ARCH    ?= $(shell uname -m)

ifeq ($(ARCH),aarch64)
  MARCH = -march=armv8.2-a+fp16+dotprod -mtune=cortex-a76
else
  # dev box: need -march for FMA at all (baseline x86-64 is SSE2, no FMA),
  # then constrain to 128-bit so FLOP/cycle is comparable to A76's NEON.
  MARCH = -march=native -mprefer-vector-width=128
endif

CFLAGS  = $(STD) $(WARN) $(OPT) $(MARCH) -Iinclude
LDFLAGS = -lm

SRC  = src/perf.c src/thermal.c src/mpc.c src/main.c
OBJ  = $(SRC:src/%.c=bin/%.o)

all: bin/critter

bin/%.o: src/%.c | bin
	$(CC) $(CFLAGS) -c $< -o $@

bin/critter: $(OBJ)
	$(CC) $(CFLAGS) $^ -o $@ $(LDFLAGS)

bin:
	@mkdir -p bin

# H3 hard gate: must report ~8 fp64 FLOP/cyc (128-bit FMA peak)
peak: bin/critter
	./bin/critter --peak-probe

# prove the kernels actually vectorised and used FMA
objdump: bin/mpc.o
	@echo "--- FMA count in mpc.o ---"
	@objdump -d bin/mpc.o | grep -cE 'vfmadd|fmla' || echo 0
	@echo "--- packed (vector) vs scalar ---"
	@objdump -d bin/mpc.o | grep -cE 'vfmadd[0-9]*pd|fmla.*v[0-9]+\.2d' || echo 0

clean:
	rm -rf bin

.PHONY: all peak objdump clean
