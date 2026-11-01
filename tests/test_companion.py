"""Проверки внешнего поведения генератора и автономного HTML-отчёта."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from test_cli import BIN, ROOT, row


class Companion(unittest.TestCase):
    def test_synthetic_generator_is_deterministic_and_truth_matches(self):
        script = ROOT / "scripts" / "generate.py"
        self.assertTrue(script.is_file(), "Генератор ещё не реализован")
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            a, b = Path(td) / "a", Path(td) / "b"
            for folder in [a, b]:
                subprocess.run(
                    [
                        sys.executable,
                        str(script),
                        "--output",
                        str(folder),
                        "--requests",
                        "10000",
                        "--seed",
                        "91",
                    ],
                    check=True,
                    capture_output=True,
                )
            for name in ["incident.tsv", "baseline.tsv", "truth.json"]:
                self.assertEqual((a / name).read_bytes(), (b / name).read_bytes())
            truth = json.loads((a / "truth.json").read_text(encoding="utf-8"))
            p = subprocess.run(
                [
                    str(BIN),
                    "analyze",
                    str(a / "incident.tsv"),
                    "--baseline",
                    str(a / "baseline.tsv"),
                    "--json",
                ],
                check=True,
                capture_output=True,
            )
            report = json.loads(p.stdout)
            for dataset in ["incident", "baseline"]:
                for key in ["requests", "errors_5xx", "bytes"]:
                    self.assertEqual(
                        report[dataset]["summary"][key], truth[dataset][key]
                    )
                self.assertEqual(
                    report[dataset]["summary"]["latency_us"]["sum"],
                    truth[dataset]["latency_sum_us"],
                )
            self.assertGreater(len(report["alerts"]), 0)

    def test_html_escapes_hostile_targets_and_preserves_large_integers(self):
        script = ROOT / "scripts" / "render_html.py"
        self.assertTrue(script.is_file(), "HTML-отчёт ещё не реализован")
        payload = '/</td><script>alert(1)</script>?x="&'
        p = subprocess.run(
            [str(BIN), "analyze", "-", "--json", "--keep-query"],
            input=row(path=payload, size=2**64 - 1).encode(),
            check=True,
            capture_output=True,
        )
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            report, output = Path(td) / "report.json", Path(td) / "report.html"
            report.write_bytes(p.stdout)
            subprocess.run(
                [sys.executable, str(script), str(report), "--output", str(output)],
                check=True,
                capture_output=True,
            )
            html = output.read_text(encoding="utf-8")
            self.assertNotIn("<script>", html)
            self.assertIn("&lt;script&gt;", html)
            self.assertIn("18 446 744 073 709 551 615", html)
            self.assertIn("Content-Security-Policy", html)
            self.assertIn('lang="ru"', html)
            self.assertNotIn("https://", html)
            self.assertIn("<svg", html)

    def test_benchmark_reports_real_runs_and_exact_counts(self):
        script = ROOT / "scripts" / "benchmark.py"
        self.assertTrue(script.is_file(), "Измеритель ещё не реализован")
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            output = Path(td) / "benchmark.json"
            subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--binary",
                    str(BIN),
                    "--sizes",
                    "200,1000",
                    "--repeats",
                    "2",
                    "--output",
                    str(output),
                    "--compiler-description",
                    "test compiler",
                ],
                check=True,
                capture_output=True,
            )
            result = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(len(result["measurements"]), 2)
            for measurement, count in zip(result["measurements"], [200, 1000]):
                self.assertEqual(measurement["requests"], count)
                self.assertEqual(len(measurement["runs"]), 2)
                self.assertGreater(measurement["median_seconds"], 0)
                self.assertGreater(measurement["median_requests_per_second"], 0)
                self.assertTrue(
                    all(x["peak_rss_bytes"] > 0 for x in measurement["runs"])
                )

    def test_script_help_and_errors_are_russian(self):
        for name in [
            "build.py",
            "generate.py",
            "benchmark.py",
            "render_html.py",
            "accuracy.py",
            "render_accuracy.py",
        ]:
            with self.subTest(script=name):
                script = ROOT / "scripts" / name
                p = subprocess.run(
                    [sys.executable, str(script), "--help"],
                    capture_output=True,
                    encoding="utf-8",
                    env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
                )
                self.assertEqual(p.returncode, 0)
                self.assertIn("Использование:", p.stdout)
                self.assertNotIn("show this help message", p.stdout)
                p = subprocess.run(
                    [sys.executable, str(script), "--unknown-option"],
                    capture_output=True,
                    encoding="utf-8",
                    env={**__import__("os").environ, "PYTHONIOENCODING": "utf-8"},
                )
                self.assertNotEqual(p.returncode, 0)
                self.assertIn("Ошибка параметров:", p.stderr)

    def test_html_rejects_unknown_schema_without_output(self):
        script = ROOT / "scripts" / "render_html.py"
        self.assertTrue(script.is_file(), "HTML-отчёт ещё не реализован")
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            source, target = Path(td) / "bad.json", Path(td) / "bad.html"
            source.write_text('{"schema_version": 99}')
            p = subprocess.run(
                [sys.executable, str(script), str(source), "--output", str(target)],
                capture_output=True,
            )
            self.assertNotEqual(p.returncode, 0)
            self.assertFalse(target.exists())


if __name__ == "__main__":
    unittest.main()
