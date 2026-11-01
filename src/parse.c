#include "logscope.h"
#include <string.h>

/* Locale-independent decimal grammar; strtoull accepts signs and whitespace. */
bool parse_u64(const char *text, uint64_t *value) {
    uint64_t n = 0;
    if (*text == '\0') return false;
    for (const unsigned char *p = (const unsigned char *)text; *p; p++) {
        if (*p < '0' || *p > '9') return false;
        unsigned digit = (unsigned)(*p - '0');
        if (n > (UINT64_MAX - digit) / 10) return false;
        n = n * 10 + digit;
    }
    *value = n;
    return true;
}

bool valid_method(const char *text) {
    size_t n = strlen(text);
    if (n == 0 || n > LS_METHOD_MAX) return false;
    for (size_t i = 0; i < n; i++)
        if (text[i] < 'A' || text[i] > 'Z') return false;
    return true;
}

ParseResult parse_record(const char *line, Record *record) {
    char copy[LS_LINE_MAX + 1];
    size_t len = strlen(line);
    if (len > LS_LINE_MAX) return PARSE_LONG;
    memcpy(copy, line, len + 1);
    char *field[6]; field[0] = copy;
    size_t columns = 1;
    for (size_t i = 0; i < len; i++) {
        if (copy[i] == '\t') {
            if (columns == 6) return PARSE_COLUMNS;
            copy[i] = '\0'; field[columns++] = copy + i + 1;
        }
    }
    if (columns != 6) return PARSE_COLUMNS;
    Record r = {0}; uint64_t status;
    if (!parse_u64(field[0], &r.timestamp_ms) ||
        !parse_u64(field[3], &status) || !parse_u64(field[4], &r.bytes) ||
        !parse_u64(field[5], &r.latency_us)) return PARSE_NUMBER;
    if (!valid_method(field[1])) return PARSE_METHOD;
    size_t n = strlen(field[2]);
    if (n == 0 || n > LS_TARGET_MAX ||
        (field[2][0] != '/' && strcmp(field[2], "*") != 0)) return PARSE_TARGET;
    for (size_t i = 0; i < n; i++) {
        unsigned char c = (unsigned char)field[2][i];
        if (c < 33 || c > 126) return PARSE_TARGET;
    }
    if (status < 100 || status > 599) return PARSE_STATUS;
    if (r.latency_us > LS_LATENCY_MAX) return PARSE_LATENCY;
    r.status = (unsigned)status;
    memcpy(r.method, field[1], strlen(field[1]) + 1);
    memcpy(r.target, field[2], n + 1);
    *record = r;
    return PARSE_OK;
}

const char *parse_reason(ParseResult result) {
    static const char *const names[] = {
        "ok", "columns", "number", "method", "target", "status", "latency",
        "line_too_long", "binary"
    };
    return (unsigned)result < PARSE_REASON_COUNT ? names[result] : "unknown";
}
