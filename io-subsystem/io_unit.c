/* io_unit.c
 *
 * Critter I/O unit.
 *
 * Reads temperature samples from the sensor at a high rate, tags each
 * one with a sequence number and a timestamp, and writes them out as
 * CSV for the downstream memory/summarization unit to consume.
 *
 * Output format (one header line, then one row per sample):
 *
 *     seq,timestamp_ms,raw_temp_cC
 *     0,1717000000123,2201
 *     1,1717000000133,2199
 *     ...
 *
 * where raw_temp_cC is temperature in hundredths of a degree Celsius.
 */

#include "sensor.h"

#include <stdio.h>
#include <stdlib.h>
#include <time.h>
#include <signal.h>

#define SAMPLE_PERIOD_NS  10000000L      /* 100 Hz */
#define FLUSH_EVERY       500
#define OUTPUT_FILE       "temperature_raw.csv"

/* set to 0 by the signal handler when the user asks us to stop */
int running = 1;

struct sample {
    unsigned long seq;
    long long ts_ms;
    int16_t value;
};

void handle_sigint(int sig)
{
    running = 0;
}

long long now_ms(void)
{
    struct timespec ts;
    clock_gettime(CLOCK_REALTIME, &ts);
    return (long long) ts.tv_sec * 1000 + ts.tv_nsec / 1000000;
}

int main(int argc, char **argv)
{
    struct sample *buf;
    unsigned long count = 0;
    unsigned long capacity = 0;
    FILE *out;
    struct timespec period;
    unsigned long i;

    signal(SIGINT, handle_sigint);

    if (argc > 1) {
        out = fopen(argv[1], "w");   /* let the caller pick the file */
    } else {
        out = fopen(OUTPUT_FILE, "w");
    }

    fprintf(out, "seq,timestamp_ms,raw_temp_cC\n");

    sensor_init();

    buf = malloc(sizeof(struct sample) * 64);
    capacity = 64;

    period.tv_sec = 0;
    period.tv_nsec = SAMPLE_PERIOD_NS;

    printf("Critter I/O unit running. Press Ctrl-C to stop.\n");

    while (running) {

        if (count >= capacity) {
            capacity = capacity * 2;
            buf = realloc(buf, sizeof(struct sample) * capacity);
        }

        buf[count].seq = count;
        buf[count].ts_ms = now_ms();
        buf[count].value = sensor_read_raw();
        count++;

        if (count % FLUSH_EVERY == 0) {
            for (i = count - FLUSH_EVERY; i < count; i++) {
                fprintf(out, "%lu,%lld,%d\n",
                        buf[i].seq, buf[i].ts_ms, buf[i].value);
            }
            fflush(out);
            printf("\rsamples collected: %lu", count);
            fflush(stdout);
        }

        nanosleep(&period, NULL);
    }

    /* write out whatever is left since the last flush */
    for (i = count - (count % FLUSH_EVERY); i < count; i++) {
        fprintf(out, "%lu,%lld,%d\n",
                buf[i].seq, buf[i].ts_ms, buf[i].value);
    }

    fclose(out);
    sensor_shutdown();
    free(buf);

    printf("\nStopped. %lu samples written.\n", count);
    return 0;
}
