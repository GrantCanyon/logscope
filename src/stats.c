#include "logscope.h"

static size_t histogram_index(uint64_t value) {
    size_t index = 0;
    while (value) { index++; value >>= 1; }
    return index;
}

bool stats_add(Stats *s, const Record *r) {
    if (r->status < 100 || r->status > 599) return false;
    if (s->requests == UINT64_MAX || s->bytes > UINT64_MAX - r->bytes ||
        s->latency_sum > UINT64_MAX - r->latency_us) return false;
    /* All component counts are <= requests: this preflight makes update atomic. */
    if (s->requests == 0) {
        s->latency_min = s->latency_max = r->latency_us;
        s->first_ms = s->last_ms = r->timestamp_ms;
    } else {
        if (r->latency_us < s->latency_min) s->latency_min = r->latency_us;
        if (r->latency_us > s->latency_max) s->latency_max = r->latency_us;
        if (r->timestamp_ms < s->first_ms) s->first_ms = r->timestamp_ms;
        if (r->timestamp_ms > s->last_ms) s->last_ms = r->timestamp_ms;
    }
    s->requests++; s->errors += r->status >= 500 ? 1U : 0U;
    s->bytes += r->bytes; s->latency_sum += r->latency_us;
    s->status[r->status]++; s->histogram[histogram_index(r->latency_us)]++;
    return true;
}

bool stats_quantile(const Stats *s, unsigned percentile, Quantile *out) {
    if (s->requests == 0 || percentile == 0 || percentile > 100) return false;
    /* ceil(n*p/100), avoiding multiplication overflow for uint64 counts. */
    uint64_t rank = (s->requests / 100) * percentile +
                    ((s->requests % 100) * percentile + 99) / 100;
    uint64_t cumulative = 0;
    for (size_t i = 0; i < 65; i++) {
        cumulative += s->histogram[i];
        if (cumulative >= rank) {
            out->lower = i == 0 ? 0 : UINT64_C(1) << (i - 1);
            out->upper = i == 64 ? UINT64_MAX : (UINT64_C(1) << i) - 1;
            return true;
        }
    }
    return false;
}
