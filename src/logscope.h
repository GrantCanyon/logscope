#ifndef LOGSCOPE_H
#define LOGSCOPE_H

/* Сохраняем стандартные C17 fopen/strcpy вместо непереносимых расширений CRT.
   Предусловия длины ключей заданы у top_add и проверяются парсером. */
#if defined(_MSC_VER) && !defined(_CRT_SECURE_NO_WARNINGS)
#define _CRT_SECURE_NO_WARNINGS
#endif

#include <stdbool.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>

#define LS_VERSION "1.0.0"
#define LS_METHOD_MAX 16
#define LS_TARGET_MAX 2048
#define LS_LINE_MAX 8192
#define LS_READ_SIZE 65536
#define LS_LATENCY_MAX UINT64_C(86400000000)
#define LS_NONE SIZE_MAX

typedef struct {
    uint64_t timestamp_ms, bytes, latency_us;
    unsigned status;
    char method[LS_METHOD_MAX + 1], target[LS_TARGET_MAX + 1];
} Record;

typedef enum {
    PARSE_OK, PARSE_COLUMNS, PARSE_NUMBER, PARSE_METHOD, PARSE_TARGET,
    PARSE_STATUS, PARSE_LATENCY, PARSE_LONG, PARSE_BINARY, PARSE_REASON_COUNT
} ParseResult;
ParseResult parse_record(const char *line, Record *record);
const char *parse_reason(ParseResult result);
bool parse_u64(const char *text, uint64_t *value);
bool valid_method(const char *text);

typedef struct {
    uint64_t requests, errors, bytes, latency_sum, latency_min, latency_max;
    uint64_t first_ms, last_ms;
    uint64_t status[600], histogram[65];
} Stats;
typedef struct { uint64_t lower, upper; } Quantile;
bool stats_add(Stats *stats, const Record *record);
bool stats_quantile(const Stats *stats, unsigned percentile, Quantile *out);

typedef struct {
    char method[LS_METHOD_MAX + 1], target[LS_TARGET_MAX + 1];
    uint64_t count, error, hash;
    size_t next, heap_pos;
} TopEntry;
typedef struct {
    TopEntry *entries;
    size_t *heads, *heap;
    size_t capacity, buckets, used;
} Top;
bool top_init(Top *top, size_t capacity);
void top_free(Top *top);
/* method/target: NUL-строки длиной <= LS_METHOD_MAX / LS_TARGET_MAX;
   вход приложения удовлетворяет этому после parse_record. */
bool top_add(Top *top, const char *method, const char *target);
const TopEntry *top_find(const Top *top, const char *method, const char *target);

typedef struct {
    uint64_t minute, requests, errors, latency_sum;
    bool occupied;
} Minute;
typedef struct {
    Minute *slots;
    size_t capacity;
    uint64_t latest, outside, evicted;
    bool initialized;
} Window;
bool window_init(Window *window, size_t capacity);
void window_free(Window *window);
bool window_add(Window *window, const Record *record);

#endif
