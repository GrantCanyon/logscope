"""Детерминированные синтетические журналы: спокойный период и сбой оплаты."""
from cli import Parser
import json
from pathlib import Path
import random

EPOCH_MS = 1767225600000

def generate(path, requests, seed, incident, cardinality=400):
    rng = random.Random(seed)
    truth = {"requests": 0, "errors_5xx": 0, "bytes": 0, "latency_sum_us": 0}
    with Path(path).open("w", encoding="ascii", newline="\n") as stream:
        for i in range(requests):
            elapsed = i * 90 * 60000 // requests
            minute = elapsed // 60000
            checkout = rng.randrange(5) == 0
            path = "/checkout" if checkout else f"/catalog/item/{rng.randrange(cardinality)}"
            method = "POST" if checkout else "GET"
            fault = incident and 45 <= minute < 55 and checkout
            status = 503 if rng.randrange(100) < (85 if fault else 1) else 200
            latency = rng.randrange(2500, 45000) + (rng.randrange(400000, 1400000) if fault else 0)
            size = rng.randrange(80, 500) if status == 503 else rng.randrange(800, 12000)
            timestamp = EPOCH_MS + elapsed
            # Единичные опоздавшие события проверяют независимость от порядка строк.
            if i and i % 997 == 0:
                timestamp -= min(elapsed, 5 * 60000)
            stream.write(f"{timestamp}\t{method}\t{path}?campaign=synthetic\t{status}\t{size}\t{latency}\n")
            truth["requests"] += 1
            truth["errors_5xx"] += status >= 500
            truth["bytes"] += size
            truth["latency_sum_us"] += latency
    return truth

def main():
    parser = Parser(description="Создать синтетический инцидент и базовый период без реальных данных.")
    parser.add_argument("--output", type=Path, default=Path("build/demo"), help="Каталог результатов")
    parser.add_argument("--requests", type=int, default=100000, help="Количество запросов в каждом журнале")
    parser.add_argument("--seed", type=int, default=2026, help="Начальное состояние генератора")
    parser.add_argument("--cardinality", type=int, default=400, help="Количество возможных товарных путей")
    args = parser.parse_args()
    if args.requests < 1 or args.cardinality < 1:
        parser.error("Количество запросов и путей должно быть положительным")
    args.output.mkdir(parents=True, exist_ok=True)
    truth = {"seed": args.seed, "cardinality": args.cardinality, "synthetic": True}
    for name in ["incident", "baseline"]:
        truth[name] = generate(args.output / f"{name}.tsv", args.requests, args.seed, name == "incident", args.cardinality)
    (args.output / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Создано: {args.output}")

if __name__ == "__main__":
    main()
