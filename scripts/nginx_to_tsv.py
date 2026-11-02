"""Потоковый адаптер точного профиля logscope_json_v1 для Nginx."""

from collections import Counter
import json
import os
import re
import sys
from cli import Parser

UINT64_MAX = (1 << 64) - 1
LATENCY_MAX_US = 86400000000
MAX_LINE_BYTES = 16384
FIELDS = frozenset(
    {
        "msec",
        "request_method",
        "request_uri",
        "status",
        "body_bytes_sent",
        "request_time",
    }
)


class AdapterError(ValueError):
    def __init__(self, reason):
        self.reason = reason
        super().__init__(reason)


class AdapterParser(Parser):
    def error(self, message):
        # argparse может включать неизвестное значение в message: его не печатаем.
        self.print_usage(sys.stderr)
        self.exit(1, "строка 0: usage\n")


def unsigned(value, limit, reason):
    if len(value) > 20:
        raise AdapterError("number")
    if not re.fullmatch(r"0|[1-9][0-9]*", value):
        raise AdapterError(reason)
    result = int(value)
    if result > limit:
        raise AdapterError(reason)
    return result


def milliseconds(value, limit, reason):
    if len(value) > 24:
        raise AdapterError("number")
    match = re.fullmatch(r"(0|[1-9][0-9]*)\.([0-9]{3})", value)
    if not match:
        raise AdapterError(reason)
    result = int(match[1]) * 1000 + int(match[2])
    if result > limit:
        raise AdapterError(reason)
    return result


def convert_record(payload, keep_query=False):
    if not isinstance(payload, dict) or set(payload) != FIELDS:
        raise AdapterError("fields")
    if any(type(value) is not str for value in payload.values()):
        raise AdapterError("field_type")
    timestamp = milliseconds(payload["msec"], UINT64_MAX, "timestamp")
    method, target = payload["request_method"], payload["request_uri"]
    if not re.fullmatch(r"[A-Z]{1,16}", method):
        raise AdapterError("method")
    if not 1 <= len(target) <= 2048 or (not target.startswith("/") and target != "*"):
        raise AdapterError("target")
    # Проверяется весь URI до удаления query, включая декодированные JSON escapes.
    if any(ord(character) < 33 or ord(character) > 126 for character in target):
        raise AdapterError("target")
    if not keep_query:
        target = target.partition("?")[0]
    status = unsigned(payload["status"], 599, "status")
    if status < 100:
        raise AdapterError("status")
    size = unsigned(payload["body_bytes_sent"], UINT64_MAX, "bytes")
    latency = (
        milliseconds(payload["request_time"], LATENCY_MAX_US // 1000, "latency") * 1000
    )
    return f"{timestamp}\t{method}\t{target}\t{status}\t{size}\t{latency}"


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise AdapterError("duplicate_field")
        result[key] = value
    return result


def reject_json_number(value):
    # В данном профиле все значения — строки; float не создаётся даже парсером JSON.
    raise AdapterError("field_type")


def parse_line(raw, keep_query=False):
    if raw.endswith(b"\n"):
        raw = raw[:-1]
        if raw.endswith(b"\r"):
            raw = raw[:-1]
    if len(raw) > MAX_LINE_BYTES:
        raise AdapterError("line_too_long")
    if b"\r" in raw or b"\n" in raw:
        raise AdapterError("line_ending")
    try:
        decoded = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise AdapterError("encoding") from None
    try:
        payload = json.loads(
            decoded,
            object_pairs_hook=unique_object,
            parse_int=reject_json_number,
            parse_float=reject_json_number,
            parse_constant=reject_json_number,
        )
    except (json.JSONDecodeError, RecursionError):
        raise AdapterError("json") from None
    return convert_record(payload, keep_query)


def silence_stream(stream):
    """Не дать повторному flush при выходе заменить IO-код 2 на Python-код 120."""
    try:
        descriptor = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(descriptor, stream.fileno())
        finally:
            os.close(descriptor)
    except OSError:
        # Основная ошибка уже зафиксирована; повторного текста исключения нет.
        try:
            stream.close()
        except OSError:
            pass


def convert_stream(source, output, keep_query=False, skip_invalid=False):
    line_number = accepted = rejected = 0
    reasons, samples = Counter(), []
    status = 0

    def note(reason):
        reasons[reason] += 1
        if len(samples) < 8:
            samples.append((line_number, reason))

    while True:
        try:
            raw = source.readline(MAX_LINE_BYTES + 2)
        except OSError:
            note("io_read")
            status = 2
            break
        if not raw:
            break
        if line_number == UINT64_MAX:
            note("counter_overflow")
            status = 3
            break
        line_number += 1
        try:
            row = parse_line(raw, keep_query)
        except AdapterError as exc:
            note(exc.reason)
            rejected += 1
            status = 3
            if not skip_invalid:
                break
            # readline имеет явный лимит; остаток длинной строки не накапливается.
            if len(raw) == MAX_LINE_BYTES + 2 and not raw.endswith(b"\n"):
                try:
                    while raw and not raw.endswith(b"\n"):
                        raw = source.readline(MAX_LINE_BYTES + 2)
                except OSError:
                    note("io_read")
                    status = 2
                    break
            continue
        try:
            output.write((row + "\n").encode("ascii"))
        except OSError:
            note("io_write")
            status = 2
            break
        accepted += 1
    try:
        output.flush()
    except OSError:
        if reasons["io_write"] == 0:
            note("io_write")
        status = 2
    return status, line_number, accepted, rejected, reasons, samples


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    parser = AdapterParser(
        description="Преобразовать профиль Nginx logscope_json_v1 в LogScope TSV; по умолчанию первая ошибка останавливает поток."
    )
    parser.add_argument("input", help="Один JSONL-файл либо - для stdin")
    parser.add_argument(
        "--keep-query",
        action="store_true",
        help="Сохранить query в TSV после проверки полного URI",
    )
    parser.add_argument(
        "--skip-invalid",
        action="store_true",
        help="Продолжать после ошибок данных; итоговый код при отклонениях всё равно 3",
    )
    args = parser.parse_args()
    own_source = args.input != "-"
    try:
        if os.name == "nt":
            import msvcrt

            if not own_source:
                msvcrt.setmode(sys.stdin.fileno(), os.O_BINARY)
            msvcrt.setmode(sys.stdout.fileno(), os.O_BINARY)
        source = open(args.input, "rb") if own_source else sys.stdin.buffer
    except OSError:
        try:
            print("строка 0: io_read", file=sys.stderr)
            sys.stderr.flush()
        except OSError:
            silence_stream(sys.stderr)
        return 2
    result = convert_stream(
        source, sys.stdout.buffer, args.keep_query, args.skip_invalid
    )
    status, lines, accepted, rejected, reasons, samples = result
    if own_source:
        try:
            source.close()
        except OSError:
            status = 2
            reasons["io_read"] += 1
            if len(samples) < 8:
                samples.append((lines, "io_read"))
    if reasons["io_write"]:
        silence_stream(sys.stdout)
    try:
        for line, reason in samples:
            print(f"строка {line}: {reason}", file=sys.stderr)
        counts = ", ".join(
            f"{reason}={count}" for reason, count in sorted(reasons.items()) if count
        )
        print(
            f"строк={lines} преобразовано={accepted} отклонено={rejected}"
            + (f"; {counts}" if counts else ""),
            file=sys.stderr,
        )
        sys.stderr.flush()
    except OSError:
        silence_stream(sys.stderr)
        return 2
    return status


if __name__ == "__main__":
    sys.exit(main())
