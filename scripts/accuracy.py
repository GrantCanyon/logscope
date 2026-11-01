"""Воспроизводимый эксперимент точности Space-Saving с независимым Counter."""

from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import random
import statistics
import sys
import tempfile
from benchmark import run_process
from cli import Parser
from generate import generate

ROOT = Path(__file__).resolve().parents[1]


def evaluate_series(truth, entries, capacity, k):
    """Проверить весь sketch и оценить top-k без произвольного штрафа за ничьи."""
    if (
        capacity < 1
        or k < 1
        or any(type(n) is not int or n < 1 for n in truth.values())
    ):
        raise ValueError("Некорректный эталон, ёмкость или k")
    count, distinct = sum(truth.values()), len(truth)
    if len(entries) > capacity:
        raise ValueError("Число слотов превышает ёмкость")
    tracked, widths, exact_count = set(), [], 0
    upper_sum = 0
    for entry in entries:
        key = (entry["method"], entry["target"])
        if key in tracked or key not in truth:
            raise ValueError("Повторный либо отсутствующий в эталоне ключ")
        tracked.add(key)
        lower, upper, error = (entry[name] for name in ("lower", "upper", "error"))
        if any(type(n) is not int or n < 0 for n in (lower, upper, error)):
            raise ValueError("Границы должны быть неотрицательными целыми")
        if lower != upper - error or not lower <= truth[key] <= upper:
            raise ValueError("Нарушена tracked-граница Space-Saving")
        widths.append(upper - lower)
        exact_count += lower == upper == truth[key]
        upper_sum += upper
    if upper_sum != count:
        raise ValueError("Сумма верхних границ не равна числу событий")
    untracked = set(truth) - tracked
    if len(entries) == capacity:
        minimum = min(entry["upper"] for entry in entries)
        if any(truth[key] > minimum for key in untracked):
            raise ValueError("Нарушена граница для untracked-ключа")
    elif untracked:
        raise ValueError("Неполный sketch потерял ключ эталона")
    if distinct <= capacity and (untracked or exact_count != distinct):
        raise ValueError("При достаточной ёмкости частоты должны быть точными")

    effective = min(k, distinct)
    recall = {
        "lower": None,
        "upper": None,
        "k_effective": effective,
        "threshold_count": None,
        "mandatory_count": 0,
        "tied_count": 0,
        "mandatory_hits": 0,
        "tied_hits": 0,
        "selected_count": min(k, len(entries)),
    }
    if effective:
        threshold = sorted(truth.values(), reverse=True)[effective - 1]
        mandatory = {key for key, n in truth.items() if n > threshold}
        tied = {key for key, n in truth.items() if n == threshold}
        selected = {(entry["method"], entry["target"]) for entry in entries[:k]}
        mandatory_hits, tied_hits = len(selected & mandatory), len(selected & tied)
        slots = effective - len(mandatory)
        recall.update(
            {
                "lower": (mandatory_hits + max(0, tied_hits - (len(tied) - slots)))
                / effective,
                "upper": (mandatory_hits + min(tied_hits, slots)) / effective,
                "threshold_count": threshold,
                "mandatory_count": len(mandatory),
                "tied_count": len(tied),
                "mandatory_hits": mandatory_hits,
                "tied_hits": tied_hits,
            }
        )
    return {
        "requests": count,
        "distinct_count": distinct,
        "tracked_count": len(entries),
        "interval_width": {
            "mean": statistics.mean(widths) if widths else None,
            "max": max(widths) if widths else None,
        },
        "exact_tracked_fraction": exact_count / len(entries) if entries else None,
        "guarantees": {
            "tracked_checked": len(entries),
            "untracked_checked": len(untracked),
            "violations": 0,
            "exact_case": distinct <= capacity,
        },
        "recall_at_k": recall,
    }


def file_hash(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def read_oracle(path):
    """Независимо прочитать созданный TSV; не использовать частоты из отчёта C."""
    requests, errors = Counter(), Counter()
    size_sum = latency_sum = 0
    with path.open(encoding="ascii") as stream:
        for line in stream:
            fields = line.rstrip("\n").split("\t")
            if len(fields) != 6:
                raise ValueError("Генератор выдал неверный TSV")
            _, method, target, status, size, latency = fields
            key = method, target.partition("?")[0]
            requests[key] += 1
            if int(status) >= 500:
                errors[key] += 1
            size_sum += int(size)
            latency_sum += int(latency)
    return {
        "counters": {"requests": requests, "errors": errors},
        "summary": {
            "requests": sum(requests.values()),
            "errors_5xx": sum(errors.values()),
            "bytes": size_sum,
            "latency_sum_us": latency_sum,
        },
    }


def validate_report(report, oracle, capacity, k):
    if report.get("schema_version") != 1:
        raise ValueError("CLI вернул неизвестную версию JSON")
    incident = report["incident"]
    summary, expected = incident["summary"], oracle["summary"]
    if any(
        summary[name] != expected[name] for name in ("requests", "errors_5xx", "bytes")
    ):
        raise ValueError("Общие счётчики CLI не совпали с независимым эталоном")
    if (
        summary["latency_us"]["sum"] != expected["latency_sum_us"]
        or incident["input"]["rejected"]
    ):
        raise ValueError("Сумма задержек или качество входа не совпали с эталоном")
    return {
        name: evaluate_series(oracle["counters"][name], incident[field], capacity, k)
        for name, field in (("requests", "top_requests"), ("errors", "top_errors"))
    }


def run_experiment(args):
    binary = args.binary.resolve()
    result = {
        "experiment_schema_version": 1,
        "measured_at_utc": datetime.now(timezone.utc).isoformat(),
        "environment": {
            "os": platform.system(),
            "os_release": platform.release(),
            "architecture": platform.machine(),
            "processor": platform.processor(),
            "logical_cpus": os.cpu_count(),
            "python": platform.python_version(),
            "compiler": args.compiler_description,
            "binary_sha256": file_hash(binary),
        },
        "parameters": {
            "requests": args.requests,
            "seed": args.seed,
            "top_k": args.top_k,
            "cardinalities": args.cardinalities,
            "capacities": args.capacities,
            "repeats": args.repeats,
            "warmup_runs": 1,
            "window_minutes": 120,
            "keep_query": False,
            "synthetic": True,
            "timing": "отдельный C-процесс: запуск, чтение, анализ и JSON; прогретый кэш",
            "memory": "пик RSS только C-процесса; Python Counter и генератор исключены",
        },
        "datasets": [],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=args.output.parent, prefix="logscope-accuracy-"
    ) as folder:
        data, output = Path(folder) / "input.tsv", Path(folder) / "report.json"
        for cardinality in args.cardinalities:
            generate(data, args.requests, args.seed, True, cardinality)
            digest, input_bytes = file_hash(data), data.stat().st_size
            oracle = read_oracle(data)
            dataset = {
                "seed": args.seed,
                "requested_cardinality": cardinality,
                "input_sha256": digest,
                "input_bytes": input_bytes,
                "truth": {
                    name: {
                        "requests": sum(counts.values()),
                        "distinct_count": len(counts),
                    }
                    for name, counts in oracle["counters"].items()
                },
                "summary": oracle["summary"],
                "execution_order": [],
                "capacities": [],
            }
            by_capacity = {
                capacity: {
                    "capacity": capacity,
                    "input_sha256": digest,
                    "metrics": None,
                    "runs": [],
                }
                for capacity in args.capacities
            }
            order_rng = random.Random(args.seed + cardinality * 1000003)
            for round_index in range(args.repeats + 1):
                order = args.capacities.copy()
                order_rng.shuffle(order)
                dataset["execution_order"].append(
                    {
                        "warmup": round_index == 0,
                        "round": round_index,
                        "capacities": order,
                    }
                )
                for capacity in order:
                    command = [
                        str(binary),
                        "analyze",
                        str(data),
                        "--json",
                        "--capacity",
                        str(capacity),
                        "--top",
                        str(capacity),
                        "--window-minutes",
                        "120",
                    ]
                    seconds, peak = run_process(command, output)
                    metrics = validate_report(
                        json.loads(output.read_bytes()), oracle, capacity, args.top_k
                    )
                    current = by_capacity[capacity]
                    if current["metrics"] is not None and metrics != current["metrics"]:
                        raise ValueError(
                            "Повтор одного потока дал разные метрики точности"
                        )
                    current["metrics"] = metrics
                    if round_index:
                        current["runs"].append(
                            {"seconds": seconds, "peak_rss_bytes": peak}
                        )
            if file_hash(data) != digest:
                raise ValueError("Входной файл изменился между измерениями")
            for capacity in args.capacities:
                current = by_capacity[capacity]
                current["median_seconds"] = statistics.median(
                    run["seconds"] for run in current["runs"]
                )
                current["median_requests_per_second"] = (
                    args.requests / current["median_seconds"]
                )
                peaks = [run["peak_rss_bytes"] for run in current["runs"]]
                current["peak_rss_bytes"] = {
                    "min": min(peaks),
                    "median": statistics.median(peaks),
                    "max": max(peaks),
                }
                dataset["capacities"].append(current)
            result["datasets"].append(dataset)
    return result


def positive_list(text):
    values = [int(value) for value in text.split(",")]
    if not values or min(values) < 1 or len(set(values)) != len(values):
        raise ValueError("Ожидается список разных положительных целых")
    return values


def main():
    parser = Parser(
        description="Измерить точность, время и память Space-Saving на одинаковом синтетическом потоке."
    )
    parser.add_argument(
        "--binary",
        type=Path,
        default=ROOT / "build" / ("logscope.exe" if os.name == "nt" else "logscope"),
        help="Исполняемый файл C-анализатора",
    )
    parser.add_argument(
        "--requests", type=int, default=100000, help="Строк на один набор данных"
    )
    parser.add_argument(
        "--cardinalities",
        default="32,256,4096",
        help="Числа возможных товарных путей через запятую",
    )
    parser.add_argument(
        "--capacities", default="16,64,256,1024", help="Ёмкости sketch через запятую"
    )
    parser.add_argument(
        "--top-k", type=int, default=10, help="Размер точного top-k для оценки"
    )
    parser.add_argument("--seed", type=int, default=2026, help="Состояние генератора")
    parser.add_argument(
        "--repeats", type=int, default=3, help="Измерений после одного прогрева"
    )
    parser.add_argument(
        "--compiler-description",
        required=True,
        help="Компилятор, версия и флаги проверяемого бинарника",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "build/accuracy.json",
        help="Файл результатов JSON",
    )
    args = parser.parse_args()
    try:
        args.cardinalities = positive_list(args.cardinalities)
        args.capacities = positive_list(args.capacities)
        if (
            max(args.capacities) > 4096
            or min(args.requests, args.top_k, args.repeats) < 1
        ):
            raise ValueError(
                "Размеры должны быть положительными, capacity не больше 4096"
            )
    except ValueError as exc:
        parser.error(str(exc))
    try:
        result = run_experiment(args)
        args.output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        print(f"Эксперимент неуспешен: {exc}", file=sys.stderr)
        return 2
    print(f"Эксперимент проверен и сохранён: {args.output}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
