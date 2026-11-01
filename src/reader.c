#include "app.h"
#include <inttypes.h>
#include <string.h>
#ifdef _WIN32
#include <fcntl.h>
#include <io.h>
#endif

bool analysis_init(Analysis *a, const Options *o) {
    *a = (Analysis){0};
    if (top_init(&a->traffic, o->capacity) && top_init(&a->errors, o->capacity) &&
        window_init(&a->window, o->window)) return true;
    analysis_free(a); return false;
}
void analysis_free(Analysis *a) {
    top_free(&a->traffic); top_free(&a->errors); window_free(&a->window);
}
static bool increment(uint64_t *n) {
    if (*n == UINT64_MAX) return false;
    ++*n; return true;
}
static bool matches(const Record *r, const Options *o) {
    return r->timestamp_ms >= o->from && (!o->has_until || r->timestamp_ms < o->until) &&
        (!o->method || strcmp(o->method, r->method) == 0) &&
        (!o->prefix || strncmp(o->prefix, r->target, strlen(o->prefix)) == 0) &&
        r->status >= o->status_min && r->status <= o->status_max && r->latency_us >= o->min_latency;
}
static int consume(Analysis *a, const Options *o, char *line, ParseResult reason) {
    InputStats *i = &a->input; Record record;
    if (!increment(&i->lines)) return 3;
    if (reason == PARSE_OK) reason = parse_record(line, &record);
    if (reason != PARSE_OK) {
        if (!increment(&i->rejected) || !increment(&i->reasons[reason])) return 3;
        if (i->sample_count < LS_SAMPLES) i->samples[i->sample_count++] = (Sample){i->lines, reason};
        if (o->strict) {
            fprintf(stderr, "Недопустимая запись: строка %" PRIu64 ", причина %s.\n", i->lines, parse_reason(reason));
            return 3;
        }
        return 0;
    }
    if (!increment(&i->valid)) return 3;
    if (!matches(&record, o)) return increment(&i->filtered) ? 0 : 3;
    if (!o->keep_query) {
        char *query = strchr(record.target, '?');
        if (query) *query = '\0';
    }
    if (!increment(&i->accepted) || !stats_add(&a->stats, &record) ||
        !top_add(&a->traffic, record.method, record.target) ||
        (record.status >= 500 && !top_add(&a->errors, record.method, record.target)) ||
        !window_add(&a->window, &record)) return 3;
    return 0;
}
int analyze_file(Analysis *a, const Options *o, const char *path) {
    bool is_stdin = strcmp(path, "-") == 0;
    FILE *f = is_stdin ? stdin : fopen(path, "rb");
    if (!f) { fputs("Не удалось открыть входной файл.\n", stderr); return 2; }
#ifdef _WIN32
    if (is_stdin && _setmode(_fileno(stdin), _O_BINARY) == -1) {
        fputs("Не удалось включить двоичное чтение stdin.\n", stderr); return 2;
    }
#endif
    unsigned char buffer[LS_READ_SIZE];
    char line[LS_LINE_MAX + 2];
    size_t used = 0, count;
    bool binary = false, long_line = false, pending = false;
    int result = 0;
    while (result == 0 && (count = fread(buffer, 1, sizeof buffer, f)) != 0) {
        for (size_t j = 0; j < count && result == 0; j++) {
            unsigned char c = buffer[j];
            if (c == '\n') {
                if (used && line[used - 1] == '\r') used--;
                line[used] = '\0';
                ParseResult reason = long_line || used > LS_LINE_MAX ? PARSE_LONG : binary ? PARSE_BINARY : PARSE_OK;
                result = consume(a, o, line, reason);
                used = 0; binary = false; long_line = false; pending = false;
            } else {
                pending = true;
                if (c == 0) binary = true;
                if (used < LS_LINE_MAX + 1) line[used++] = (char)c;
                else long_line = true;
            }
        }
    }
    if (result == 0 && ferror(f)) { fputs("Ошибка чтения входного потока.\n", stderr); result = 2; }
    if (result == 0 && pending) {
        line[used] = '\0';
        result = consume(a, o, line, long_line || used > LS_LINE_MAX ? PARSE_LONG : binary ? PARSE_BINARY : PARSE_OK);
    }
    if (!is_stdin && fclose(f) != 0 && result == 0) result = 2;
    if (result == 3 && !(o->strict && a->input.rejected))
        fputs("Переполнение счётчика: отчёт не сформирован.\n", stderr);
    return result;
}
