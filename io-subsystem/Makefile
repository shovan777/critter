CC     = gcc
CFLAGS = -std=gnu11 -O2

io_unit: io_unit.c sensor_sim.c sensor.h
	$(CC) $(CFLAGS) -o io_unit io_unit.c sensor_sim.c

clean:
	rm -f io_unit temperature_raw.csv

.PHONY: clean
