#include "logscope.h"
#include <stdlib.h>
#include <string.h>

static uint64_t key_hash(const char *method, const char *target) {
    uint64_t h = UINT64_C(14695981039346656037);
    for (const unsigned char *p = (const unsigned char *)method; *p; p++) {
        h ^= *p; h *= UINT64_C(1099511628211);
    }
    h ^= 0; h *= UINT64_C(1099511628211);
    for (const unsigned char *p = (const unsigned char *)target; *p; p++) {
        h ^= *p; h *= UINT64_C(1099511628211);
    }
    return h;
}

bool top_init(Top *t, size_t capacity) {
    *t = (Top){0};
    if (capacity == 0 || capacity > 4096) return false;
    t->capacity = capacity; t->buckets = capacity * 2 + 1;
    t->entries = calloc(capacity, sizeof *t->entries);
    t->heads = malloc(t->buckets * sizeof *t->heads);
    t->heap = malloc(capacity * sizeof *t->heap);
    if (!t->entries || !t->heads || !t->heap) { top_free(t); return false; }
    for (size_t i = 0; i < t->buckets; i++) t->heads[i] = LS_NONE;
    return true;
}

void top_free(Top *t) {
    free(t->entries); free(t->heads); free(t->heap); *t = (Top){0};
}

static size_t find_index(const Top *t, const char *method, const char *target, uint64_t h) {
    size_t index = t->heads[h % t->buckets];
    while (index != LS_NONE) {
        const TopEntry *e = &t->entries[index];
        if (e->hash == h && strcmp(e->method, method) == 0 && strcmp(e->target, target) == 0)
            return index;
        index = e->next;
    }
    return LS_NONE;
}

const TopEntry *top_find(const Top *t, const char *method, const char *target) {
    size_t i = find_index(t, method, target, key_hash(method, target));
    return i == LS_NONE ? NULL : &t->entries[i];
}

/* Stable slot index breaks ties, so replaying a stream is deterministic. */
static bool less(const Top *t, size_t a, size_t b) {
    uint64_t x = t->entries[a].count, y = t->entries[b].count;
    return x < y || (x == y && a < b);
}

static void swap_heap(Top *t, size_t a, size_t b) {
    size_t temp = t->heap[a]; t->heap[a] = t->heap[b]; t->heap[b] = temp;
    t->entries[t->heap[a]].heap_pos = a; t->entries[t->heap[b]].heap_pos = b;
}

static void sink(Top *t, size_t pos) {
    while (pos * 2 + 1 < t->used) {
        size_t child = pos * 2 + 1;
        if (child + 1 < t->used && less(t, t->heap[child + 1], t->heap[child])) child++;
        if (!less(t, t->heap[child], t->heap[pos])) break;
        swap_heap(t, pos, child); pos = child;
    }
}

bool top_add(Top *t, const char *method, const char *target) {
    uint64_t h = key_hash(method, target);
    size_t index = find_index(t, method, target, h);
    if (index != LS_NONE) {
        TopEntry *e = &t->entries[index];
        if (e->count == UINT64_MAX) return false;
        e->count++; sink(t, e->heap_pos); return true;
    }
    uint64_t error = 0;
    bool replacing = t->used == t->capacity;
    if (replacing) {
        index = t->heap[0];
        TopEntry *old = &t->entries[index];
        if (old->count == UINT64_MAX) return false;
        error = old->count;
        size_t *link = &t->heads[old->hash % t->buckets];
        while (*link != index) link = &t->entries[*link].next;
        *link = old->next;
    } else {
        index = t->used; t->heap[t->used] = index;
        t->entries[index].heap_pos = t->used; t->used++;
    }
    TopEntry *e = &t->entries[index];
    strcpy(e->method, method); strcpy(e->target, target);
    e->hash = h; e->count = error + 1; e->error = error;
    size_t bucket = h % t->buckets;
    e->next = t->heads[bucket]; t->heads[bucket] = index;
    if (replacing) sink(t, 0);
    else {
        size_t pos = e->heap_pos;
        while (pos > 0) {
            size_t parent = (pos - 1) / 2;
            if (!less(t, t->heap[pos], t->heap[parent])) break;
            swap_heap(t, pos, parent); pos = parent;
        }
    }
    return true;
}
