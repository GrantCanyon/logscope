"""Независимые ручные контракты эксперимента: частоты, ничьи и границы sketch."""

from collections import Counter
import importlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_cli import BIN, ROOT

SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))


def key(name):
    return "GET", "/" + name


def entry(name, upper, error=0):
    return {
        "method": "GET",
        "target": "/" + name,
        "upper": upper,
        "lower": upper - error,
        "error": error,
    }


class Accuracy(unittest.TestCase):
    def module(self):
        self.assertTrue(
            (SCRIPTS / "accuracy.py").is_file(), "Эксперимент ещё не реализован"
        )
        return importlib.import_module("accuracy")

    def test_hand_checked_ties_capacity_and_exact_cases(self):
        evaluate = self.module().evaluate_series
        truth = Counter({key("a"): 2, key("b"): 1, key("c"): 1})
        cases = [
            (3, [entry("a", 2), entry("b", 1), entry("c", 1)], 0.5, 1, 0),
            (2, [entry("a", 2), entry("c", 2, 1)], 0.5, 1, 1),
            (1, [entry("c", 4, 3)], 0, 0.5, 3),
        ]
        for capacity, entries, low, high, width in cases:
            with self.subTest(capacity=capacity):
                result = evaluate(truth, entries, capacity, 2)
                self.assertEqual(result["recall_at_k"]["lower"], low)
                self.assertEqual(result["recall_at_k"]["upper"], high)
                self.assertEqual(result["recall_at_k"]["k_effective"], 2)
                self.assertEqual(result["interval_width"]["max"], width)
                self.assertEqual(result["guarantees"]["violations"], 0)
        r = evaluate(
            Counter({key(x): 1 for x in "abcd"}), [entry(x, 1) for x in "abcd"], 4, 2
        )
        self.assertEqual((r["recall_at_k"]["lower"], r["recall_at_k"]["upper"]), (0, 1))
        self.assertEqual(r["interval_width"]["max"], 0)
        r = evaluate(
            Counter({key("a"): 5, key("b"): 3, key("c"): 1}),
            [entry("a", 5), entry("b", 3), entry("c", 1)],
            3,
            2,
        )
        self.assertEqual((r["recall_at_k"]["lower"], r["recall_at_k"]["upper"]), (1, 1))
        r = evaluate(Counter({key("a"): 5}), [entry("a", 5)], 3, 10)
        self.assertEqual(r["recall_at_k"]["k_effective"], 1)
        self.assertEqual(r["recall_at_k"]["lower"], 1)

    def test_empty_series_has_no_misleading_recall(self):
        r = self.module().evaluate_series(Counter(), [], 2, 10)
        self.assertEqual(r["requests"], 0)
        self.assertEqual(r["distinct_count"], 0)
        self.assertIsNone(r["recall_at_k"]["lower"])
        self.assertIsNone(r["recall_at_k"]["upper"])
        self.assertIsNone(r["interval_width"]["max"])

    def test_validator_rejects_corrupted_tracked_and_untracked_bounds(self):
        evaluate = self.module().evaluate_series
        with self.assertRaises(ValueError):
            evaluate(
                Counter({key("a"): 3, key("b"): 1}),
                [entry("a", 2), entry("b", 2)],
                2,
                1,
            )
        # Каждый tracked-интервал допустим и сумма upper=12, но пропущенный /a=10 > min(upper)=6.
        with self.assertRaises(ValueError):
            evaluate(
                Counter({key("a"): 10, key("b"): 1, key("c"): 1}),
                [entry("b", 6, 5), entry("c", 6, 5)],
                2,
                1,
            )
        with self.assertRaises(ValueError):
            evaluate(Counter({key("a"): 2}), [entry("a", 1), entry("a", 1)], 2, 1)
        with self.assertRaises(ValueError):
            evaluate(Counter({key("a"): 2, key("b"): 2}), [entry("a", 4, 3)], 3, 1)

    def test_oracle_separates_methods_queries_and_error_stream(self):
        module = self.module()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            path = Path(td) / "source.tsv"
            path.write_text(
                "0\tGET\t/a?x\t200\t10\t1\n1\tGET\t/a?y\t503\t20\t2\n2\tPOST\t/a?z\t200\t30\t3\n",
                encoding="ascii",
            )
            oracle = module.read_oracle(path)
            self.assertEqual(
                oracle["counters"]["requests"],
                Counter({("GET", "/a"): 2, ("POST", "/a"): 1}),
            )
            self.assertEqual(oracle["counters"]["errors"], Counter({("GET", "/a"): 1}))
            self.assertEqual(
                oracle["summary"],
                {"requests": 3, "errors_5xx": 1, "bytes": 60, "latency_sum_us": 6},
            )

    def test_experiment_reuses_identical_input_and_separates_measurements(self):
        self.module()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            paths = [Path(td) / "one.json", Path(td) / "two.json"]
            reports = []
            for path in paths:
                p = subprocess.run(
                    [
                        sys.executable,
                        str(SCRIPTS / "accuracy.py"),
                        "--binary",
                        str(BIN),
                        "--requests",
                        "200",
                        "--cardinalities",
                        "3,12",
                        "--capacities",
                        "1,16",
                        "--top-k",
                        "2",
                        "--seed",
                        "17",
                        "--repeats",
                        "1",
                        "--compiler-description",
                        "тестовая сборка",
                        "--output",
                        str(path),
                    ],
                    capture_output=True,
                    timeout=30,
                )
                self.assertEqual(
                    p.returncode, 0, p.stderr.decode("utf-8", errors="replace")
                )
                reports.append(json.loads(path.read_text(encoding="utf-8")))
            self.assertEqual(reports[0]["experiment_schema_version"], 1)
            for a, b in zip(reports[0]["datasets"], reports[1]["datasets"]):
                self.assertEqual(a["input_sha256"], b["input_sha256"])
                self.assertEqual(a["execution_order"], b["execution_order"])
                self.assertEqual(a["requested_cardinality"], b["requested_cardinality"])
                self.assertEqual(a["truth"]["requests"]["requests"], 200)
                for x, y in zip(a["capacities"], b["capacities"]):
                    self.assertEqual(x["input_sha256"], a["input_sha256"])
                    self.assertEqual(x["metrics"], y["metrics"])
                    self.assertEqual(set(x["metrics"]), {"requests", "errors"})
                    self.assertEqual(len(x["runs"]), 1)
                    self.assertGreater(x["runs"][0]["seconds"], 0)
                    self.assertGreater(x["runs"][0]["peak_rss_bytes"], 0)
                    self.assertEqual(
                        x["metrics"]["requests"]["guarantees"]["violations"], 0
                    )
                    if x["capacity"] >= x["metrics"]["requests"]["distinct_count"]:
                        self.assertEqual(
                            x["metrics"]["requests"]["interval_width"]["max"], 0
                        )


class AccuracyHTML(unittest.TestCase):
    def test_standalone_report_escapes_environment_and_shows_both_series(self):
        script = SCRIPTS / "render_accuracy.py"
        self.assertTrue(
            script.is_file(), "Представление эксперимента ещё не реализовано"
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            source, output = Path(td) / "report.json", Path(td) / "report.html"
            subprocess.run(
                [
                    sys.executable,
                    str(SCRIPTS / "accuracy.py"),
                    "--binary",
                    str(BIN),
                    "--requests",
                    "100",
                    "--cardinalities",
                    "3",
                    "--capacities",
                    "1,8",
                    "--top-k",
                    "2",
                    "--repeats",
                    "1",
                    "--compiler-description",
                    "<script>alert(1)</script>",
                    "--output",
                    str(source),
                ],
                check=True,
                capture_output=True,
            )
            p = subprocess.run(
                [sys.executable, str(script), str(source), "--output", str(output)],
                capture_output=True,
            )
            self.assertEqual(
                p.returncode, 0, p.stderr.decode("utf-8", errors="replace")
            )
            html = output.read_text(encoding="utf-8")
            self.assertIn('lang="ru"', html)
            self.assertIn("Content-Security-Policy", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertNotIn("<script", html)
            self.assertNotIn("https://", html)
            self.assertIn("Все запросы", html)
            self.assertIn("Только 5xx", html)
            self.assertIn("не доверительный интервал", html)
            self.assertIn("<svg", html)
            self.assertIn("Counter", html)
            self.assertIn("SHA256", html)

    def test_unknown_experiment_version_has_no_successful_output(self):
        script = SCRIPTS / "render_accuracy.py"
        self.assertTrue(
            script.is_file(), "Представление эксперимента ещё не реализовано"
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            source, output = Path(td) / "report.json", Path(td) / "report.html"
            source.write_text('{"experiment_schema_version":99}', encoding="utf-8")
            p = subprocess.run(
                [sys.executable, str(script), str(source), "--output", str(output)],
                capture_output=True,
            )
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
