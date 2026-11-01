#include "logscope.h"
#include <stdlib.h>

bool window_init(Window *w, size_t capacity) {
    *w = (Window){0};
    if (capacity == 0 || capacity > 10080) return false;
    w->slots = calloc(capacity, sizeof *w->slots);
    if (!w->slots) return false;
    w->capacity = capacity; return true;
}

void window_free(Window *w) { free(w->slots); *w = (Window){0}; }

bool window_add(Window *w, const Record *r) {
    uint64_t minute = r->timestamp_ms / 60000;
    if (!w->initialized) { w->latest = minute; w->initialized = true; }
    if (minute > w->latest) {
        uint64_t delta = minute - w->latest;
        size_t steps = delta < w->capacity ? (size_t)delta : w->capacity;
        for (size_t i = 0; i < steps; i++) {
            size_t index = (size_t)((w->latest + 1 + i) % w->capacity);
            Minute *m = &w->slots[index];
            if (m->occupied) {
                if (w->evicted > UINT64_MAX - m->requests) return false;
                w->evicted += m->requests; *m = (Minute){0};
            }
        }
        w->latest = minute;
    }
    if (w->latest - minute >= w->capacity) {
        if (w->outside == UINT64_MAX) return false;
        w->outside++; return true;
    }
    Minute *m = &w->slots[minute % w->capacity];
    if (!m->occupied) { m->minute = minute; m->occupied = true; }
    if (m->requests == UINT64_MAX || m->latency_sum > UINT64_MAX - r->latency_us) return false;
    m->requests++; m->errors += r->status >= 500 ? 1U : 0U; m->latency_sum += r->latency_us;
    return true;
}
