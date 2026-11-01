#ifndef LOGSCOPE_APP_H
#define LOGSCOPE_APP_H
#include "logscope.h"
#define LS_SAMPLES 8

typedef struct {
    const char *input, *baseline, *method, *prefix;
    uint64_t from, until, min_latency, alert_min;
    unsigned status_min, status_max;
    size_t capacity, window, top;
    double alert_rate;
    bool has_until, strict, keep_query, json;
} Options;
typedef struct {
    uint64_t line;
    ParseResult reason;
} Sample;
typedef struct {
    uint64_t lines, valid, rejected, filtered, accepted;
    uint64_t reasons[PARSE_REASON_COUNT];
    Sample samples[LS_SAMPLES];
    size_t sample_count;
} InputStats;
typedef struct {
    Stats stats;
    Top traffic, errors;
    Window window;
    InputStats input;
} Analysis;

bool analysis_init(Analysis *a, const Options *o);
void analysis_free(Analysis *a);
int analyze_file(Analysis *a, const Options *o, const char *path);
bool report_write(FILE *out, const Analysis *incident, const Analysis *baseline, const Options *o);
#endif
