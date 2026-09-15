/*
 * ============================================================================
 *  Critter Prototype -- Memory-Focused Unit
 *  Outlier Removal and Monthly Summarization of Temperature Logs
 * ============================================================================
 *
 *  ROLE IN THE CRITTER DECOMPOSITION
 *  ---------------------------------
 *  The Critter product is split into three units. This file is an exemplar of
 *  the MEMORY-FOCUSED unit: it takes a large body of temperature readings,
 *  holds them in RAM, discards bad readings, and reduces what remains to a
 *  compact summary for off-line analysis. Its siblings are the I/O unit
 *  (high-rate acquisition) and the compute unit (HVAC behavior prediction).
 *
 *  WHAT THIS PROGRAM DOES
 *  ----------------------
 *    1. Reads a log of timestamped temperature readings into memory.
 *    2. Computes the mean and standard deviation of all readings.
 *    3. Flags any reading more than SIGMA_THRESHOLD standard deviations from
 *       the mean as an outlier, and reports how many were found.
 *    4. Writes the surviving readings to a cleaned log file.
 *    5. Groups the surviving readings by calendar month (January through
 *       December, aggregated across every year in the log) and writes a
 *       per-month summary: count, mean, minimum, and maximum.
 *
 *  DESIGN INTENT -- READ THIS BEFORE "FIXING" ANYTHING
 *  ---------------------------------------------------
 *  This is a PROTOTYPE EXEMPLAR, not the shipping unit. It is deliberately
 *  UNOPTIMIZED and UN-HARDENED, in keeping with a KISS directive:
 *
 *    - the entire data set is held in memory at once;
 *    - separate buffers are allocated for the raw readings, the kept
 *      readings, and a sorted copy of the temperatures, with no buffer
 *      reuse and no in-place tricks;
 *    - the mean and standard deviation are computed in separate passes
 *      rather than by a single-pass streaming algorithm;
 *    - the input file is read twice: once to count the readings so the
 *      buffers can be sized exactly, and again to load them.
 *
 *  Memory and speed optimizations are intentionally OUT OF SCOPE. The point
 *  of the exemplar is to be easy to reason about and easy to re-shape later.
 *
 *  INPUT FORMAT
 *  ------------
 *  Plain text, one reading per line, no header:
 *
 *      MM/DD/YYYY HH:MM:SS,<temperature_celsius>
 *
 *  for example:
 *
 *      07/14/2024 15:22:41,26.118
 *
 *  The readings need not be in chronological order.
 *
 *  BUILD AND RUN
 *  -------------
 *      gcc -O2 -Wall -Wextra -Wpedantic -std=c11 -o critter_mem critter_mem.c -lm
 *      ./critter_mem sample_temps.txt
 *
 *  Produces critter_cleaned.txt and critter_summary.csv in the working
 *  directory.
 * ============================================================================
 */

#include <stdio.h>
#include <stdlib.h>
#include <math.h>

/* ---- Tunable parameters --------------------------------------------------- */

/* A reading further than this many standard deviations from the mean is
   treated as an outlier and discarded. */
#define SIGMA_THRESHOLD 3.0

/* Longest input line we are prepared to read, including the newline. */
#define MAX_LINE_LENGTH 128

/* Calendar months, January through December. */
#define NUM_MONTHS 12

/* Output file names. */
#define CLEANED_FILE "critter_cleaned.txt"
#define SUMMARY_FILE "critter_summary.csv"

/* ---- Data types ---------------------------------------------------------- */

/* One temperature reading, with its timestamp broken into fields. */
typedef struct {
    int    month;        /* 1 - 12  */
    int    day;          /* 1 - 31  */
    int    year;         /* e.g. 2024 */
    int    hour;         /* 0 - 23  */
    int    minute;       /* 0 - 59  */
    int    second;       /* 0 - 59  */
    double temperature;  /* degrees Celsius */
} Reading;

/* Running totals for one calendar month, accumulated across all years. */
typedef struct {
    long   count;   /* how many readings fell in this month */
    double sum;     /* sum of their temperatures, for the mean */
    double minimum; /* coldest reading seen */
    double maximum; /* warmest reading seen */
} MonthlySummary;

/* Month names, for the summary file. Index 0 is January. */
static const char *MONTH_NAMES[NUM_MONTHS] = {
    "January", "February", "March",     "April",   "May",      "June",
    "July",    "August",   "September", "October", "November", "December"
};

/* ============================================================================
 *  Reading the input file
 *
 *  The file is read in two passes. The first pass counts the lines so the
 *  buffers can be allocated at exactly the right size; the second pass parses
 *  the readings into memory. Two passes are simpler to follow than a buffer
 *  that grows as it fills, which is why it is done this way here.
 * ==========================================================================*/

/* Pass 1: count the lines in the file. */
static long count_lines(FILE *file)
{
    char line[MAX_LINE_LENGTH];
    long count = 0;

    while (fgets(line, (int)sizeof(line), file) != NULL) {
        count++;
    }
    return count;
}

/* Parse one "MM/DD/YYYY HH:MM:SS,temp" line into a Reading.
   Returns 1 on success, 0 if the line did not match the expected format. */
static int parse_line(const char *line, Reading *reading)
{
    int fields_matched = sscanf(line, "%d/%d/%d %d:%d:%d,%lf",
                                &reading->month,
                                &reading->day,
                                &reading->year,
                                &reading->hour,
                                &reading->minute,
                                &reading->second,
                                &reading->temperature);

    return (fields_matched == 7) ? 1 : 0;
}

/* Pass 2: parse up to 'capacity' readings into 'readings'.
   Returns the number of readings actually stored. */
static long read_readings(FILE *file, Reading *readings, long capacity)
{
    char line[MAX_LINE_LENGTH];
    long stored = 0;

    while (stored < capacity && fgets(line, (int)sizeof(line), file) != NULL) {
        if (parse_line(line, &readings[stored])) {
            stored++;
        }
        /* Lines that do not parse are skipped. */
    }
    return stored;
}

/* ============================================================================
 *  Statistics
 *
 *  Each statistic gets its own straightforward loop. Combining them into a
 *  single pass would be faster but harder to read, so they are kept separate.
 * ==========================================================================*/

/* Arithmetic mean of the temperatures. */
static double compute_mean(const Reading *readings, long count)
{
    double sum = 0.0;
    long i;

    for (i = 0; i < count; i++) {
        sum += readings[i].temperature;
    }
    return sum / (double)count;
}

/* Population standard deviation of the temperatures. */
static double compute_std_dev(const Reading *readings, long count, double mean)
{
    double sum_of_squares = 0.0;
    long i;

    for (i = 0; i < count; i++) {
        double difference = readings[i].temperature - mean;
        sum_of_squares += difference * difference;
    }
    return sqrt(sum_of_squares / (double)count);
}

/* Comparison function handed to qsort, for sorting temperatures ascending. */
static int compare_temperatures(const void *first, const void *second)
{
    double a = *(const double *)first;
    double b = *(const double *)second;

    if (a < b) {
        return -1;
    }
    if (a > b) {
        return 1;
    }
    return 0;
}

/* Median temperature.
   Copies the temperatures into a scratch buffer, sorts it, and takes the
   middle value. The copy exists so the caller's data stays untouched. */
static double compute_median(const Reading *readings, long count)
{
    double *temperatures = malloc((size_t)count * sizeof(double));
    double median;
    long i;

    if (temperatures == NULL) {
        fprintf(stderr, "compute_median: out of memory\n");
        return 0.0;
    }

    for (i = 0; i < count; i++) {
        temperatures[i] = readings[i].temperature;
    }

    qsort(temperatures, (size_t)count, sizeof(double), compare_temperatures);

    if (count % 2 == 1) {
        median = temperatures[count / 2];
    } else {
        median = (temperatures[count / 2 - 1] + temperatures[count / 2]) / 2.0;
    }

    free(temperatures);
    return median;
}

/* ============================================================================
 *  Outlier removal
 * ==========================================================================*/

/* Copy every reading within SIGMA_THRESHOLD standard deviations of the mean
   into 'kept'. Returns how many readings were kept; the caller can subtract
   that from the total to learn how many outliers were removed.

   'kept' must have room for 'count' readings, since in the worst case nothing
   is discarded. */
static long remove_outliers(const Reading *readings, long count,
                            double mean, double std_dev,
                            Reading *kept)
{
    long kept_count = 0;
    long i;

    for (i = 0; i < count; i++) {
        double z_score = fabs(readings[i].temperature - mean) / std_dev;

        if (z_score <= SIGMA_THRESHOLD) {
            kept[kept_count] = readings[i];
            kept_count++;
        }
    }
    return kept_count;
}

/* Print the readings that were discarded, so the operator can see what the
   filter caught. Recomputing the z-score here rather than remembering it
   keeps remove_outliers simple. */
static void report_outliers(const Reading *readings, long count,
                            double mean, double std_dev)
{
    long i;

    printf("\nOutliers removed:\n");
    printf("  %-21s  %10s  %8s\n", "timestamp", "temp (C)", "z-score");

    for (i = 0; i < count; i++) {
        double z_score = fabs(readings[i].temperature - mean) / std_dev;

        if (z_score > SIGMA_THRESHOLD) {
            printf("  %02d/%02d/%04d %02d:%02d:%02d  %10.3f  %8.2f\n",
                   readings[i].month, readings[i].day, readings[i].year,
                   readings[i].hour, readings[i].minute, readings[i].second,
                   readings[i].temperature, z_score);
        }
    }
}

/* ============================================================================
 *  Summarization by calendar month
 *
 *  All readings from the same month are pooled regardless of year, so a log
 *  spanning 2023 to 2025 yields twelve rows: every January together, every
 *  February together, and so on. This is what surfaces the seasonal pattern
 *  an HVAC analysis cares about.
 * ==========================================================================*/

static void summarize_by_month(const Reading *readings, long count,
                               MonthlySummary *summary)
{
    int month_index;
    long i;

    for (month_index = 0; month_index < NUM_MONTHS; month_index++) {
        summary[month_index].count = 0;
        summary[month_index].sum = 0.0;
        summary[month_index].minimum = 0.0;
        summary[month_index].maximum = 0.0;
    }

    for (i = 0; i < count; i++) {
        double temperature = readings[i].temperature;

        /* Month field is 1-based in the log, 0-based in the array. */
        month_index = readings[i].month - 1;

        if (summary[month_index].count == 0) {
            /* First reading for this month seeds the min and max. */
            summary[month_index].minimum = temperature;
            summary[month_index].maximum = temperature;
        } else {
            if (temperature < summary[month_index].minimum) {
                summary[month_index].minimum = temperature;
            }
            if (temperature > summary[month_index].maximum) {
                summary[month_index].maximum = temperature;
            }
        }

        summary[month_index].count++;
        summary[month_index].sum += temperature;
    }
}

/* ============================================================================
 *  Output files
 * ==========================================================================*/

/* Write the surviving readings in the same format as the input, so the
   cleaned log can be fed straight back into this or any other unit.
   Input order is preserved. */
static void write_cleaned_file(const Reading *readings, long count)
{
    FILE *file = fopen(CLEANED_FILE, "w");
    long i;

    if (file == NULL) {
        fprintf(stderr, "could not open %s for writing\n", CLEANED_FILE);
        return;
    }

    for (i = 0; i < count; i++) {
        fprintf(file, "%02d/%02d/%04d %02d:%02d:%02d,%.3f\n",
                readings[i].month, readings[i].day, readings[i].year,
                readings[i].hour, readings[i].minute, readings[i].second,
                readings[i].temperature);
    }

    fclose(file);
}

/* Write the per-month summary as CSV. Months with no readings are emitted
   with a zero count and blank statistics. */
static void write_summary_file(const MonthlySummary *summary)
{
    FILE *file = fopen(SUMMARY_FILE, "w");
    int month_index;

    if (file == NULL) {
        fprintf(stderr, "could not open %s for writing\n", SUMMARY_FILE);
        return;
    }

    fprintf(file, "month,month_name,reading_count,mean_temp_c,min_temp_c,max_temp_c\n");

    for (month_index = 0; month_index < NUM_MONTHS; month_index++) {
        if (summary[month_index].count > 0) {
            double mean = summary[month_index].sum /
                          (double)summary[month_index].count;

            fprintf(file, "%d,%s,%ld,%.3f,%.3f,%.3f\n",
                    month_index + 1,
                    MONTH_NAMES[month_index],
                    summary[month_index].count,
                    mean,
                    summary[month_index].minimum,
                    summary[month_index].maximum);
        } else {
            fprintf(file, "%d,%s,0,,,\n",
                    month_index + 1, MONTH_NAMES[month_index]);
        }
    }

    fclose(file);
}

/* Echo the summary to the console as well, so a run is readable at a glance. */
static void print_summary(const MonthlySummary *summary)
{
    int month_index;

    printf("\nMonthly summary (all years pooled):\n");
    printf("  %-10s  %10s  %10s  %10s  %10s\n",
           "month", "count", "mean (C)", "min (C)", "max (C)");

    for (month_index = 0; month_index < NUM_MONTHS; month_index++) {
        if (summary[month_index].count > 0) {
            double mean = summary[month_index].sum /
                          (double)summary[month_index].count;

            printf("  %-10s  %10ld  %10.3f  %10.3f  %10.3f\n",
                   MONTH_NAMES[month_index],
                   summary[month_index].count,
                   mean,
                   summary[month_index].minimum,
                   summary[month_index].maximum);
        } else {
            printf("  %-10s  %10ld  %10s  %10s  %10s\n",
                   MONTH_NAMES[month_index], 0L, "-", "-", "-");
        }
    }
}

/* ============================================================================
 *  Entry point
 * ==========================================================================*/

int main(int argc, char **argv)
{
    FILE *input_file = NULL;
    Reading *raw_readings = NULL;
    Reading *kept_readings = NULL;
    MonthlySummary summary[NUM_MONTHS];

    long line_count = 0;
    long total_count = 0;
    long kept_count = 0;
    long removed_count = 0;

    double raw_mean, raw_std_dev;
    double kept_mean, kept_std_dev, kept_median;

    if (argc < 2) {
        fprintf(stderr, "usage: %s <input_file>\n", argv[0]);
        return 1;
    }

    /* ---- Open the log and size the buffers ---- */

    input_file = fopen(argv[1], "r");
    if (input_file == NULL) {
        fprintf(stderr, "could not open %s\n", argv[1]);
        return 1;
    }

    line_count = count_lines(input_file);
    if (line_count <= 0) {
        fprintf(stderr, "%s contains no readings\n", argv[1]);
        fclose(input_file);
        return 1;
    }
    rewind(input_file);

    /* One buffer for the raw log, one for the readings that survive
       filtering. The second is sized for the worst case in which no reading
       is discarded. */
    raw_readings = malloc((size_t)line_count * sizeof(Reading));
    kept_readings = malloc((size_t)line_count * sizeof(Reading));

    if (raw_readings == NULL || kept_readings == NULL) {
        fprintf(stderr, "could not allocate memory for %ld readings\n",
                line_count);
        free(raw_readings);
        free(kept_readings);
        fclose(input_file);
        return 1;
    }

    /* ---- Load the readings ---- */

    total_count = read_readings(input_file, raw_readings, line_count);
    fclose(input_file);

    if (total_count <= 0) {
        fprintf(stderr, "no readings in %s matched the expected format\n",
                argv[1]);
        free(raw_readings);
        free(kept_readings);
        return 1;
    }

    printf("Read %ld readings from %s\n", total_count, argv[1]);
    if (total_count < line_count) {
        printf("Skipped %ld malformed line(s)\n", line_count - total_count);
    }

    /* ---- Characterize the raw data ---- */

    raw_mean = compute_mean(raw_readings, total_count);
    raw_std_dev = compute_std_dev(raw_readings, total_count, raw_mean);

    printf("\nBefore filtering:\n");
    printf("  count   = %ld\n", total_count);
    printf("  mean    = %.3f C\n", raw_mean);
    printf("  std dev = %.3f C\n", raw_std_dev);
    printf("  outlier threshold = %.1f sigma, so anything outside "
           "[%.3f, %.3f] C\n",
           SIGMA_THRESHOLD,
           raw_mean - SIGMA_THRESHOLD * raw_std_dev,
           raw_mean + SIGMA_THRESHOLD * raw_std_dev);

    /* ---- Remove outliers ---- */

    report_outliers(raw_readings, total_count, raw_mean, raw_std_dev);

    kept_count = remove_outliers(raw_readings, total_count,
                                 raw_mean, raw_std_dev, kept_readings);
    removed_count = total_count - kept_count;

    kept_mean = compute_mean(kept_readings, kept_count);
    kept_std_dev = compute_std_dev(kept_readings, kept_count, kept_mean);
    kept_median = compute_median(kept_readings, kept_count);

    printf("\nOutliers found and removed: %ld\n", removed_count);
    printf("\nAfter filtering:\n");
    printf("  count   = %ld\n", kept_count);
    printf("  mean    = %.3f C\n", kept_mean);
    printf("  std dev = %.3f C\n", kept_std_dev);
    printf("  median  = %.3f C\n", kept_median);

    /* ---- Write the outputs ---- */

    write_cleaned_file(kept_readings, kept_count);
    printf("\nWrote %ld cleaned readings to %s\n", kept_count, CLEANED_FILE);

    summarize_by_month(kept_readings, kept_count, summary);
    print_summary(summary);
    write_summary_file(summary);
    printf("\nWrote monthly summary to %s\n", SUMMARY_FILE);

    free(raw_readings);
    free(kept_readings);
    return 0;
}
