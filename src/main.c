#include "app.h"
#include <signal.h>
#include <stdlib.h>
#include <string.h>

static void usage(FILE *f) {
    fputs("LogScope " LS_VERSION " — потоковое исследование HTTP-журналов\n"
          "Использование: logscope analyze ФАЙЛ|- [параметры]\n"
          "  --baseline ФАЙЛ       сравнение с базовым периодом\n"
          "  --json                машиночитаемый отчёт, версия 1\n"
          "  --strict              остановка на первой повреждённой строке\n"
          "  --from MS --until MS  полуоткрытый интервал времени [from, until)\n"
          "  --method МЕТОД        фильтр GET, POST и т. п.\n"
          "  --status MIN:MAX      включительный диапазон кодов (100..599)\n"
          "  --path-prefix ПРЕФИКС фильтр исходного request-target\n"
          "  --min-latency-us N     минимальное время ответа, микросекунды\n"
          "  --keep-query          сохранять query в ключах конечных точек\n"
          "  --capacity N          ёмкость каждого top (1..4096, по умолчанию 128)\n"
          "  --window-minutes N    окно событий (1..10080, по умолчанию 120)\n"
          "  --top N               число выводимых ключей (1..4096, по умолчанию 10)\n"
          "  --alert-min-requests N минимальный объём минуты (по умолчанию 20)\n"
          "  --alert-rate ПРОЦЕНТ  порог 5xx (0..100, по умолчанию 10)\n"
          "Вход: epoch_ms<TAB>METHOD<TAB>target<TAB>status<TAB>bytes<TAB>latency_us\n"
          "Коды завершения: 0 успех, 1 параметры, 2 ввод/вывод, 3 данные/переполнение.\n", f);
}
/* Собственная десятичная грамматика исключает NaN, Infinity и зависимость от locale. */
static bool percent(const char *s, double *out) {
    double n = 0, scale = 0.1; bool dot = false, digit = false;
    for (; *s; s++) {
        if (*s == '.' && !dot && digit) { dot = true; digit = false; continue; }
        if (*s < '0' || *s > '9') return false;
        digit = true;
        if (dot) { n += (double)(*s - '0') * scale; scale *= 0.1; }
        else { n = n * 10 + (double)(*s - '0'); }
        if (n > 100) return false;
    }
    if (!digit) return false;
    *out = n; return true;
}
static bool options(int argc, char **argv, Options *o) {
    *o = (Options){.status_min=100, .status_max=599, .capacity=128, .window=120,
                   .top=10, .alert_min=20, .alert_rate=10};
    if (argc < 3 || strcmp(argv[1], "analyze") != 0) return false;
    o->input = argv[2];
    for (int i = 3; i < argc; i++) {
        const char *name = argv[i];
        if (strcmp(name, "--json") == 0) { o->json = true; continue; }
        if (strcmp(name, "--strict") == 0) { o->strict = true; continue; }
        if (strcmp(name, "--keep-query") == 0) { o->keep_query = true; continue; }
        if (++i == argc) return false;
        const char *v = argv[i]; uint64_t n;
        if (strcmp(name, "--baseline") == 0) { if (strcmp(v, "-") == 0) return false; o->baseline = v; }
        else if (strcmp(name, "--method") == 0) { if (!valid_method(v)) return false; o->method = v; }
        else if (strcmp(name, "--path-prefix") == 0) {
            if (!*v || strlen(v) > LS_TARGET_MAX) return false;
            for (const unsigned char *p = (const unsigned char *)v; *p; p++) if (*p < 33 || *p > 126) return false;
            o->prefix = v;
        } else if (strcmp(name, "--alert-rate") == 0) { if (!percent(v, &o->alert_rate)) return false; }
        else if (strcmp(name, "--status") == 0) {
            char copy[16]; if (strlen(v) >= sizeof copy) return false; strcpy(copy, v);
            char *colon = strchr(copy, ':'); uint64_t low, high;
            if (!colon) return false;
            *colon++ = '\0';
            if (!parse_u64(copy, &low) || !parse_u64(colon, &high) || low < 100 || high > 599 || low > high) return false;
            o->status_min = (unsigned)low; o->status_max = (unsigned)high;
        } else {
            if (!parse_u64(v, &n)) return false;
            if (strcmp(name, "--from") == 0) o->from = n;
            else if (strcmp(name, "--until") == 0) { o->until = n; o->has_until = true; }
            else if (strcmp(name, "--min-latency-us") == 0) { if (n > LS_LATENCY_MAX) return false; o->min_latency = n; }
            else if (strcmp(name, "--capacity") == 0) { if (n < 1 || n > 4096) return false; o->capacity = (size_t)n; }
            else if (strcmp(name, "--top") == 0) { if (n < 1 || n > 4096) return false; o->top = (size_t)n; }
            else if (strcmp(name, "--window-minutes") == 0) { if (n < 1 || n > 10080) return false; o->window = (size_t)n; }
            else if (strcmp(name, "--alert-min-requests") == 0) { if (n < 1) return false; o->alert_min = n; }
            else return false;
        }
    }
    return !o->has_until || o->from <= o->until;
}
int main(int argc, char **argv) {
#ifdef SIGPIPE
    signal(SIGPIPE, SIG_IGN);
#endif
    if (argc == 2 && strcmp(argv[1], "--help") == 0) { usage(stdout); return fflush(stdout) == 0 ? 0 : 2; }
    if (argc == 2 && strcmp(argv[1], "--version") == 0) { puts(LS_VERSION); return fflush(stdout) == 0 ? 0 : 2; }
    Options o;
    if (!options(argc, argv, &o)) { usage(stderr); return 1; }
    Analysis incident = {0}, baseline = {0};
    if (!analysis_init(&incident, &o) || (o.baseline && !analysis_init(&baseline, &o))) {
        analysis_free(&incident); analysis_free(&baseline);
        fputs("Недостаточно памяти для заданной ёмкости.\n", stderr); return 2;
    }
    int result = analyze_file(&incident, &o, o.input);
    if (!result && o.baseline) result = analyze_file(&baseline, &o, o.baseline);
    if (!result && (!report_write(stdout, &incident, o.baseline ? &baseline : NULL, &o) || fflush(stdout) != 0)) {
        fputs("Не удалось записать отчёт.\n", stderr); result = 2;
    }
    analysis_free(&incident); analysis_free(&baseline); return result;
}
