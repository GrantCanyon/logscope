#ifdef NDEBUG
#undef NDEBUG
#endif
#include "logscope.h"
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static void parser_contract(void) {
    Record r;
    assert(parse_record("60001\tGET\t/a?x=1\t503\t123\t3200", &r) == PARSE_OK);
    assert(r.timestamp_ms == 60001 && r.status == 503 && r.bytes == 123);
    assert(r.latency_us == 3200 && strcmp(r.method, "GET") == 0);
    assert(strcmp(r.target, "/a?x=1") == 0);
    assert(parse_record("18446744073709551615\tOPTIONS\t*\t100\t18446744073709551615\t86400000000", &r) == PARSE_OK);
    const char *bad[] = {
        "", "0\tGET\t/a\t200\t0", "0\tGET\t/a\t200\t0\t1\textra",
        "-1\tGET\t/a\t200\t0\t1", "18446744073709551616\tGET\t/a\t200\t0\t1",
        "0\tget\t/a\t200\t0\t1", "0\tABCDEFGHIJKLMNOPQ\t/a\t200\t0\t1",
        "0\tGET\tbad\t200\t0\t1", "0\tGET\t/a b\t200\t0\t1",
        "0\tGET\t/a\t99\t0\t1", "0\tGET\t/a\t600\t0\t1",
        "0\tGET\t/a\t200\t+1\t1", "0\tGET\t/a\t200\t0\t86400000001",
        "0\tGET\t/a\t200\t0\t", "0\tGET\t/a\t200\t0\t1.5"
    };
    for (size_t i = 0; i < sizeof bad / sizeof bad[0]; i++)
        assert(parse_record(bad[i], &r) != PARSE_OK);
}

static void stats_contract(void) {
    Stats s = {0}; Record r = {0}; Quantile q;
    assert(!stats_quantile(&s, 50, &q));
    uint64_t values[] = {0, 1, 2, 3, 4, 7, 8, 100};
    r.status = 200; r.bytes = 2;
    for (size_t i = 0; i < 8; i++) { r.latency_us = values[i]; assert(stats_add(&s, &r)); }
    assert(s.requests == 8 && s.bytes == 16 && s.latency_sum == 125);
    assert(s.latency_min == 0 && s.latency_max == 100 && s.status[200] == 8);
    assert(stats_quantile(&s, 50, &q) && q.lower == 2 && q.upper == 3);
    assert(stats_quantile(&s, 95, &q) && q.lower == 64 && q.upper == 127);
    r.status = 503; r.latency_us = 1; assert(stats_add(&s, &r)); assert(s.errors == 1);
    Stats before = s; s.bytes = UINT64_MAX; before.bytes = UINT64_MAX;
    assert(!stats_add(&s, &r)); assert(memcmp(&s, &before, sizeof s) == 0);
    s = (Stats){0}; s.requests = UINT64_MAX; assert(!stats_add(&s, &r));
    s = (Stats){0}; s.latency_sum = UINT64_MAX; assert(!stats_add(&s, &r));
}

static void top_contract(void) {
    Top t; assert(top_init(&t, 2));
    assert(top_add(&t, "GET", "/a")); assert(top_add(&t, "GET", "/a"));
    assert(top_add(&t, "GET", "/b")); assert(top_add(&t, "GET", "/c"));
    const TopEntry *a = top_find(&t, "GET", "/a");
    const TopEntry *c = top_find(&t, "GET", "/c");
    assert(a && a->count == 2 && a->error == 0);
    assert(c && c->count == 2 && c->error == 1);
    assert(!top_find(&t, "GET", "/b"));
    for (size_t i = 0; i < 10000; i++) {
        char key[32]; snprintf(key, sizeof key, "/key%zu", i % 71);
        assert(top_add(&t, "POST", key));
        assert(t.used == 2);
    }
    top_free(&t);
    assert(top_init(&t, 1)); assert(top_add(&t, "GET", "/a"));
    t.entries[0].count = UINT64_MAX; assert(!top_add(&t, "GET", "/a"));
    top_free(&t);
}

static void window_contract(void) {
    Window w; Record r = {0}; r.status = 503;
    assert(window_init(&w, 3));
    r.timestamp_ms = 600000; assert(window_add(&w, &r)); /* минута 10 */
    r.timestamp_ms = 480000; assert(window_add(&w, &r)); /* минута 8 */
    r.timestamp_ms = 420000; assert(window_add(&w, &r)); /* вне окна */
    assert(w.outside == 1);
    r.timestamp_ms = 720000; assert(window_add(&w, &r)); /* минута 12 вытесняет минуту 8 */
    assert(w.evicted == 1);
    assert(w.slots[10 % 3].requests == 1);
    r.timestamp_ms = UINT64_MAX; assert(window_add(&w, &r));
    assert(w.evicted == 3 && w.outside == 1);
    window_free(&w);
}

int main(void) {
    parser_contract(); stats_contract(); top_contract(); window_contract();
    puts("Модульные проверки пройдены: разбор, гистограмма, арифметика, вытеснения и окно событий");
    return 0;
}
