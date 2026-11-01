CC ?= cc
PYTHON ?= python3
CFLAGS ?= -O2
WARNINGS = -std=c17 -Wall -Wextra -Wpedantic -Wconversion -Wshadow -Werror -Isrc
CORE = src/parse.c src/stats.c src/top.c src/window.c
APP = src/main.c src/reader.c src/report.c

.PHONY: all test sanitize
all: build/logscope

build:
	mkdir -p build

build/logscope: $(CORE) $(APP) src/logscope.h src/app.h | build
	$(CC) $(WARNINGS) $(CFLAGS) $(CORE) $(APP) $(LDFLAGS) -o $@

build/unit: $(CORE) tests/unit.c src/logscope.h | build
	$(CC) $(WARNINGS) $(CFLAGS) $(CORE) tests/unit.c $(LDFLAGS) -o $@

test: build/logscope build/unit
	./build/unit
	$(PYTHON) -m unittest discover -s tests -v

sanitize:
	$(PYTHON) scripts/build.py --cc "$(CC)" --sanitize
	$(PYTHON) scripts/build.py --cc "$(CC)" --sanitize --unit
	./build/unit
	$(PYTHON) -m unittest discover -s tests -v
