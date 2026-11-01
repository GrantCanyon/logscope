#include "app.h"
#include <inttypes.h>
#include <float.h>
#include <stdlib.h>
#include <string.h>

static double rate(uint64_t errors, uint64_t requests) {
    return requests ? (double)errors * 100.0 / (double)requests : 0;
}
static void json_string(FILE *out, const char *s) {
    fputc('"', out);
    for (const unsigned char *p = (const unsigned char *)s; *p; p++) {
        if (*p == '"' || *p == '\\') fputc('\\', out);
        if (*p < 32 || *p == '<' || *p == '>' || *p == '&') fprintf(out, "\\u%04x", (unsigned)*p);
        else fputc(*p, out);
    }
    fputc('"', out);
}
static void ratio(FILE *out, double numerator, double denominator) {
    if (denominator == 0) fputs("null", out);
    else fprintf(out, "%.10g", numerator / denominator);
}
static void quantile(FILE *out, const Stats *s, unsigned p) {
    Quantile q;
    if (!stats_quantile(s, p, &q)) fputs("null", out);
    else fprintf(out, "{\"lower\":%" PRIu64 ",\"upper\":%" PRIu64 "}", q.lower, q.upper);
}
static void summary(FILE *out, const Stats *s) {
    fprintf(out, "{\"requests\":%" PRIu64 ",\"errors_5xx\":%" PRIu64 ",\"bytes\":%" PRIu64 ",\"error_rate_pct\":", s->requests, s->errors, s->bytes);
    ratio(out, (double)s->errors * 100, (double)s->requests);
    fputs(",\"first_ms\":", out);
    if (s->requests) fprintf(out, "%" PRIu64, s->first_ms); else fputs("null", out);
    fputs(",\"last_ms\":", out);
    if (s->requests) fprintf(out, "%" PRIu64, s->last_ms); else fputs("null", out);
    fputs(",\"status_codes\":{", out); bool comma = false;
    for (unsigned i = 100; i < 600; i++) if (s->status[i]) {
        fprintf(out, "%s\"%u\":%" PRIu64, comma ? "," : "", i, s->status[i]); comma = true;
    }
    fprintf(out, "},\"latency_us\":{\"sum\":%" PRIu64 ",\"min\":", s->latency_sum);
    if (s->requests) fprintf(out, "%" PRIu64, s->latency_min); else fputs("null", out);
    fputs(",\"max\":", out);
    if (s->requests) fprintf(out, "%" PRIu64, s->latency_max); else fputs("null", out);
    fputs(",\"mean\":", out); ratio(out, (double)s->latency_sum, (double)s->requests);
    fputs(",\"p50\":", out); quantile(out, s, 50);
    fputs(",\"p95\":", out); quantile(out, s, 95);
    fputs(",\"p99\":", out); quantile(out, s, 99);
    fputs("}}", out);
}
static int compare_entries(const void *a, const void *b) {
    const TopEntry *x = *(const TopEntry *const *)a, *y = *(const TopEntry *const *)b;
    if (x->count != y->count) return x->count > y->count ? -1 : 1;
    int result = strcmp(x->method, y->method);
    return result ? result : strcmp(x->target, y->target);
}
static void top_report(FILE *out, const Top *t, size_t limit, const TopEntry **ordered, bool json) {
    for (size_t i = 0; i < t->used; i++) ordered[i] = &t->entries[i];
    qsort(ordered, t->used, sizeof *ordered, compare_entries);
    if (json) fputc('[', out);
    for (size_t i = 0; i < t->used && i < limit; i++) {
        const TopEntry *e = ordered[i];
        if (json) {
            if (i) fputc(',', out);
            fputs("{\"method\":", out); json_string(out, e->method);
            fputs(",\"target\":", out); json_string(out, e->target);
            fprintf(out, ",\"lower\":%" PRIu64 ",\"upper\":%" PRIu64 ",\"error\":%" PRIu64 "}", e->count-e->error, e->count, e->error);
        } else fprintf(out, "  %s %s  [%" PRIu64 ", %" PRIu64 "]\n", e->method, e->target, e->count-e->error, e->count);
    }
    if (json) fputc(']', out);
}
static void input_report(FILE *out, const InputStats *i) {
    fprintf(out, "{\"lines\":%" PRIu64 ",\"valid\":%" PRIu64 ",\"rejected\":%" PRIu64 ",\"filtered\":%" PRIu64 ",\"accepted\":%" PRIu64 ",\"reasons\":{", i->lines, i->valid, i->rejected, i->filtered, i->accepted);
    bool comma = false;
    for (unsigned r = 1; r < PARSE_REASON_COUNT; r++) if (i->reasons[r]) {
        fprintf(out, "%s\"%s\":%" PRIu64, comma ? "," : "", parse_reason((ParseResult)r), i->reasons[r]); comma = true;
    }
    fputs("},\"samples\":[", out);
    for (size_t j = 0; j < i->sample_count; j++)
        fprintf(out, "%s{\"line\":%" PRIu64 ",\"reason\":\"%s\"}", j ? "," : "", i->samples[j].line, parse_reason(i->samples[j].reason));
    fputs("]}", out);
}
/* Порядок обхода кольца задаётся временем; отдельной сортировки не требуется. */
static const Minute *minute_at(const Window *w, size_t index) {
    uint64_t first = w->latest >= w->capacity - 1 ? w->latest - (w->capacity - 1) : 0;
    uint64_t minute = first + index;
    const Minute *m = &w->slots[minute % w->capacity];
    return w->initialized && minute <= w->latest && m->occupied && m->minute == minute ? m : NULL;
}
static void window_report(FILE *out, const Window *w) {
    fprintf(out, "{\"capacity_minutes\":%zu,\"outside_window\":%" PRIu64 ",\"evicted_requests\":%" PRIu64 ",\"buckets\":[", w->capacity, w->outside, w->evicted);
    bool comma = false;
    for (size_t j = 0; j < w->capacity; j++) {
        const Minute *m = minute_at(w, j); if (!m) continue;
        fprintf(out, "%s{\"minute\":%" PRIu64 ",\"requests\":%" PRIu64 ",\"errors_5xx\":%" PRIu64 ",\"latency_sum_us\":%" PRIu64 ",\"error_rate_pct\":%.10g}", comma ? "," : "", m->minute, m->requests, m->errors, m->latency_sum, rate(m->errors,m->requests));
        comma = true;
    }
    fputs("]}", out);
}
static void analysis_report(FILE *out, const Analysis *a, const Options *o, const TopEntry **ordered) {
    fputs("{\"summary\":", out); summary(out, &a->stats);
    fputs(",\"input\":", out); input_report(out, &a->input);
    fputs(",\"top_requests\":", out); top_report(out, &a->traffic, o->top, ordered, true);
    fputs(",\"top_errors\":", out); top_report(out, &a->errors, o->top, ordered, true);
    fputs(",\"window\":", out); window_report(out, &a->window);
    fputc('}', out);
}
/* Несколько округлений double не должны превращать точное равенство в отказ.
   Допуск в процентных пунктах: 8*DBL_EPSILON*max(1, порог), до 5,4e-13. */
static bool at_least(double actual, double threshold) {
    double tolerance = 8.0 * DBL_EPSILON * (threshold > 1.0 ? threshold : 1.0);
    return actual >= threshold || threshold - actual <= tolerance;
}
static bool alerted(const Minute *m, const Analysis *b, const Options *o) {
    double r = rate(m->errors, m->requests);
    if (m->requests < o->alert_min || !at_least(r, o->alert_rate)) return false;
    if (b && b->stats.requests) {
        double br = rate(b->stats.errors, b->stats.requests);
        if (!at_least(r, 3 * br) || !at_least(r, br + 5)) return false;
    }
    return true;
}
static void alerts(FILE *out, const Analysis *a, const Analysis *b, const Options *o) {
    bool comma = false;
    if (o->json) fputc('[', out);
    for (size_t j = 0; j < a->window.capacity; j++) {
        const Minute *m = minute_at(&a->window, j);
        if (!m || !alerted(m, b, o)) continue;
        if (o->json) fprintf(out, "%s{\"minute\":%" PRIu64 ",\"requests\":%" PRIu64 ",\"errors_5xx\":%" PRIu64 ",\"error_rate_pct\":%.10g,\"rule\":\"elevated_5xx\"}", comma ? "," : "", m->minute, m->requests, m->errors, rate(m->errors,m->requests));
        else fprintf(out, "ПРЕДУПРЕЖДЕНИЕ: минута %" PRIu64 ", запросы %" PRIu64 ", 5xx %.3f%%\n", m->minute, m->requests, rate(m->errors,m->requests));
        comma = true;
    }
    if (o->json) fputc(']', out);
    else if (!comma) fputs("Предупреждений в сохранённом окне нет.\n", out);
}
bool report_write(FILE *out, const Analysis *a, const Analysis *b, const Options *o) {
    const TopEntry **ordered = malloc(o->capacity * sizeof *ordered);
    if (!ordered) return false;
    if (o->json) {
        fprintf(out, "{\"schema_version\":1,\"tool_version\":\"%s\",\"settings\":{\"capacity\":%zu,\"top\":%zu,\"window_minutes\":%zu,\"keep_query\":%s,\"alert_min_requests\":%" PRIu64 ",\"alert_rate_pct\":%.10g},\"incident\":", LS_VERSION, o->capacity, o->top, o->window, o->keep_query ? "true" : "false", o->alert_min, o->alert_rate);
        analysis_report(out, a, o, ordered);
        fputs(",\"baseline\":", out);
        if (b) analysis_report(out, b, o, ordered); else fputs("null", out);
        fputs(",\"comparison\":", out);
        if (!b) fputs("null", out);
        else {
            fputs("{\"request_ratio\":", out); ratio(out, (double)a->stats.requests, (double)b->stats.requests);
            fputs(",\"error_rate_delta_pp\":", out);
            if (a->stats.requests && b->stats.requests) fprintf(out, "%.10g", rate(a->stats.errors, a->stats.requests) - rate(b->stats.errors, b->stats.requests)); else fputs("null", out);
            fputs(",\"mean_latency_ratio\":", out);
            if (a->stats.requests && b->stats.requests && b->stats.latency_sum)
                ratio(out, (double)a->stats.latency_sum / (double)a->stats.requests, (double)b->stats.latency_sum / (double)b->stats.requests);
            else fputs("null", out);
            fputc('}', out);
        }
        fputs(",\"alerts\":", out); alerts(out, a, b, o); fputs("}\n", out);
    } else {
        fprintf(out, "LogScope %s — отчёт по инциденту\nЗапросы: %" PRIu64 " | 5xx: %" PRIu64 " (%.3f%%) | Байты: %" PRIu64 "\nСтроки: %" PRIu64 " | Отклонено: %" PRIu64 " | Отфильтровано: %" PRIu64 "\n", LS_VERSION, a->stats.requests, a->stats.errors, rate(a->stats.errors,a->stats.requests), a->stats.bytes, a->input.lines, a->input.rejected, a->input.filtered);
        if (a->stats.requests) {
            fprintf(out, "Задержка: средняя %.3f мкс; минимум %" PRIu64 "; максимум %" PRIu64 "\n", (double)a->stats.latency_sum/(double)a->stats.requests, a->stats.latency_min, a->stats.latency_max);
            const unsigned ps[] = {50,95,99};
            for (size_t j=0; j<3; j++) { Quantile q; if (stats_quantile(&a->stats, ps[j], &q)) fprintf(out,"  p%u в [%" PRIu64 ", %" PRIu64 "] мкс\n",ps[j],q.lower,q.upper); }
        }
        if (b) {
            fprintf(out, "Базовый период: запросы %" PRIu64 ", 5xx %" PRIu64 "\n", b->stats.requests, b->stats.errors);
            if (a->stats.requests && b->stats.requests) fprintf(out, "Изменение доли 5xx: %+.3f п.п.; отношение количества запросов: %.3f\n",rate(a->stats.errors,a->stats.requests)-rate(b->stats.errors,b->stats.requests),(double)a->stats.requests/(double)b->stats.requests);
        }
        fputs("Конечные точки по запросам — границы количества [нижняя, верхняя]:\n",out); top_report(out,&a->traffic,o->top,ordered,false);
        fputs("Конечные точки по 5xx — границы количества [нижняя, верхняя]:\n",out); top_report(out,&a->errors,o->top,ordered,false);
        fprintf(out,"Окно: %zu минут; вне окна %" PRIu64 "; вытеснено запросов %" PRIu64 "\n",a->window.capacity,a->window.outside,a->window.evicted);
        alerts(out,a,b,o);
        fputs("Ранжирование top может быть приближённым. Предупреждения — эвристика, не доказательство причины.\n",out);
    }
    free(ordered); return !ferror(out);
}
