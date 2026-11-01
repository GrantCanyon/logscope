"""Сквозные контракты с независимыми эталонными счётчиками Python."""

import collections
import json
import os
from pathlib import Path
import random
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
BIN = Path(
    os.environ.get(
        "LOGSCOPE_BIN",
        ROOT / "build" / ("logscope.exe" if os.name == "nt" else "logscope"),
    )
)


def row(t=0, method="GET", path="/a", status=200, size=10, latency=100):
    return f"{t}\t{method}\t{path}\t{status}\t{size}\t{latency}\n"


class CLI(unittest.TestCase):
    def run_cli(self, data="", *args, code=0):
        self.assertTrue(BIN.is_file(), "CLI LogScope ещё не реализован или не собран")
        p = subprocess.run(
            [str(BIN), "analyze", "-", "--json", *args],
            input=data.encode() if isinstance(data, str) else data,
            capture_output=True,
            timeout=15,
        )
        self.assertEqual(p.returncode, code, p.stderr.decode(errors="replace"))
        if code:
            self.assertEqual(
                p.stdout, b"", "При неудачном анализе stdout должен оставаться пустым"
            )
            return p
        return json.loads(p.stdout)

    def test_exact_summary_and_query_grouping(self):
        d = self.run_cli(
            row(path="/a?secret=1", latency=1)
            + row(t=60001, path="/a?x=2", status=503, latency=1000)
            + row(t=60002, method="POST", path="/a", status=404, latency=3)
        )
        self.assertEqual(d["schema_version"], 1)
        s = d["incident"]["summary"]
        self.assertEqual((s["requests"], s["errors_5xx"], s["bytes"]), (3, 1, 30))
        self.assertEqual(s["status_codes"], {"200": 1, "404": 1, "503": 1})
        self.assertAlmostEqual(s["error_rate_pct"], 100 / 3, places=5)
        self.assertEqual(s["latency_us"]["sum"], 1004)
        self.assertEqual(s["latency_us"]["p50"], {"lower": 2, "upper": 3})
        self.assertEqual(s["latency_us"]["p95"], {"lower": 512, "upper": 1023})
        self.assertEqual((s["first_ms"], s["last_ms"]), (0, 60002))
        top = d["incident"]["top_requests"]
        self.assertEqual(
            (top[0]["method"], top[0]["target"], top[0]["lower"], top[0]["upper"]),
            ("GET", "/a", 2, 2),
        )

    def test_empty_and_filtered_stream(self):
        for data, args in [("", []), (row(), ["--method", "POST"])]:
            d = self.run_cli(data, *args)
            s = d["incident"]["summary"]
            self.assertEqual(s["requests"], 0)
            self.assertIsNone(s["latency_us"]["mean"])
            self.assertIsNone(s["latency_us"]["p99"])
            self.assertIsNone(s["first_ms"])
            self.assertEqual(d["alerts"], [])

    def test_filters_are_conjunctive_and_time_interval_half_open(self):
        data = "".join(
            [
                row(t=99),
                row(t=100, path="/api/x", status=502, latency=1000),
                row(t=200, path="/api/x", status=502, latency=1000),
                row(t=150, path="/api/x", status=200, latency=1000),
                row(t=150, path="/api/x", status=502, latency=1),
                row(t=150, path="/other", status=502, latency=1000),
                row(t=150, method="POST", path="/api/x", status=502, latency=1000),
            ]
        )
        d = self.run_cli(
            data,
            "--from",
            "100",
            "--until",
            "200",
            "--method",
            "GET",
            "--status",
            "500:599",
            "--path-prefix",
            "/api",
            "--min-latency-us",
            "1000",
        )
        self.assertEqual(d["incident"]["summary"]["requests"], 1)
        self.assertEqual(d["incident"]["input"]["filtered"], 6)

    def test_keep_query_and_json_escaping(self):
        target = '/quote"back\\slash?q=<script>'
        d = self.run_cli(row(path=target), "--keep-query")
        self.assertEqual(d["incident"]["top_requests"][0]["target"], target)

    def test_malformed_rows_are_accounted_without_leaking_content(self):
        data = (
            row()
            + "BAD-PRIVATE-CONTENT\n"
            + row(status=999)
            + row(latency=86400000001)
            + row(size=-1)
        )
        d = self.run_cli(data)
        i = d["incident"]["input"]
        self.assertEqual((i["lines"], i["valid"], i["rejected"]), (5, 1, 4))
        self.assertEqual(
            i["reasons"], {"columns": 1, "number": 1, "status": 1, "latency": 1}
        )
        self.assertNotIn("PRIVATE", json.dumps(d))
        self.assertEqual([x["line"] for x in i["samples"]], [2, 3, 4, 5])

    def test_strict_rejection_has_line_number_and_no_report(self):
        p = self.run_cli(row() + "secret-malformed\n" + row(), "--strict", code=3)
        self.assertIn("строка 2".encode(), p.stderr)
        self.assertNotIn(b"secret-malformed", p.stderr)

    def test_reader_recovers_after_long_and_binary_lines(self):
        data = (
            b"x" * 150000
            + b"\n"
            + row().encode()
            + b"0\tGET\t/a\x00junk\t200\t0\t1\n"
            + row(status=503).encode()
        )
        d = self.run_cli(data)
        i = d["incident"]["input"]
        self.assertEqual((i["lines"], i["rejected"], i["accepted"]), (4, 2, 2))
        self.assertEqual(i["reasons"], {"line_too_long": 1, "binary": 1})

    def test_crlf_final_line_and_ctrl_z(self):
        data = row().replace("\n", "\r\n") + row(status=503).rstrip("\n")
        self.assertEqual(self.run_cli(data)["incident"]["summary"]["requests"], 2)
        self.assertEqual(
            self.run_cli(b"\x1a\n" + row().encode())["incident"]["input"]["lines"], 2
        )

    def test_unsigned_limits_overflow_and_long_target(self):
        self.run_cli(row(size=2**64 - 1) + row(size=1), code=3)
        d = self.run_cli(row(t=2**64 - 1, size=2**64 - 1, latency=86400000000))
        self.assertEqual(d["incident"]["summary"]["bytes"], 2**64 - 1)
        bad = (
            row(t=2**64)
            + row(path="/" + "a" * 2048)
            + row(method="A" * 17)
            + row(path="/\x7f")
            + row(path="/кириллица")
        )
        self.assertEqual(self.run_cli(bad)["incident"]["input"]["rejected"], 5)

    def test_window_late_records_and_large_jump(self):
        data = "".join(row(t=m * 60000) for m in [10, 8, 7, 12, 11, 9])
        w = self.run_cli(data, "--window-minutes", "3")["incident"]["window"]
        self.assertEqual([b["minute"] for b in w["buckets"]], [10, 11, 12])
        self.assertEqual((w["outside_window"], w["evicted_requests"]), (2, 1))
        w = self.run_cli(data + row(t=999999 * 60000), "--window-minutes", "3")[
            "incident"
        ]["window"]
        self.assertEqual(len(w["buckets"]), 1)
        self.assertEqual(w["evicted_requests"], 4)

    def test_space_saving_bounds_against_exact_randomized_oracle(self):
        for capacity in [1, 2, 7, 64]:
            rng = random.Random(912 + capacity)
            keys = [f"/endpoint/{rng.randrange(40)}" for _ in range(2500)]
            statuses = [503 if rng.randrange(5) == 0 else 200 for _ in keys]
            d = self.run_cli(
                "".join(row(path=k, status=s) for k, s in zip(keys, statuses)),
                "--capacity",
                str(capacity),
                "--top",
                str(capacity),
            )
            for name, actual in [
                ("top_requests", collections.Counter(keys)),
                (
                    "top_errors",
                    collections.Counter(k for k, s in zip(keys, statuses) if s == 503),
                ),
            ]:
                top = d["incident"][name]
                self.assertLessEqual(len(top), capacity)
                self.assertEqual(sum(x["upper"] for x in top), sum(actual.values()))
                for x in top:
                    self.assertLessEqual(x["lower"], actual[x["target"]])
                    self.assertGreaterEqual(x["upper"], actual[x["target"]])
                    if capacity >= len(actual):
                        self.assertEqual(x["lower"], x["upper"])
                minimum = min(x["upper"] for x in top)
                tracked = {x["target"] for x in top}
                for key, count in actual.items():
                    if key not in tracked:
                        self.assertLessEqual(count, minimum)

    def test_top_order_deterministic_for_ties(self):
        data = row(path="/z") + row(path="/a") + row(method="POST", path="/a")
        a = self.run_cli(data)
        b = self.run_cli(data)
        self.assertEqual(a, b)
        self.assertEqual(
            [(x["method"], x["target"]) for x in a["incident"]["top_requests"]],
            [("GET", "/a"), ("GET", "/z"), ("POST", "/a")],
        )

    def test_baseline_deltas_and_alert_criteria(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            baseline = Path(td) / "baseline.tsv"
            baseline.write_text(row(latency=100) * 99 + row(status=503, latency=100))
            data = row(latency=200) * 80 + row(status=503, latency=200) * 20
            d = self.run_cli(data, "--baseline", str(baseline))
            c = d["comparison"]
            self.assertEqual(c["request_ratio"], 1)
            self.assertEqual(c["error_rate_delta_pp"], 19)
            self.assertEqual(c["mean_latency_ratio"], 2)
            self.assertEqual(len(d["alerts"]), 1)
            self.assertEqual(d["alerts"][0]["minute"], 0)
            baseline.write_text(row(status=503) * 50 + row() * 50)
            self.assertEqual(
                self.run_cli(data, "--baseline", str(baseline))["alerts"], []
            )
            baseline.write_text("")
            d = self.run_cli(data, "--baseline", str(baseline))
            self.assertIsNone(d["comparison"]["request_ratio"])
            self.assertEqual(len(d["alerts"]), 1)

    def test_alert_minimum_volume(self):
        self.assertEqual(self.run_cli(row(status=503) * 19)["alerts"], [])
        self.assertEqual(len(self.run_cli(row(status=503) * 20)["alerts"]), 1)
        self.assertEqual(
            self.run_cli(row(status=503) * 20, "--alert-min-requests", "21")["alerts"],
            [],
        )

    def test_cli_rejects_invalid_values(self):
        for args in [
            ("--capacity", "0"),
            ("--capacity", "4097"),
            ("--window-minutes", "10081"),
            ("--from", "2", "--until", "1"),
            ("--status", "500:200"),
            ("--alert-rate", "101"),
            ("--method", "get"),
            ("--top", "0"),
            ("--unknown",),
            ("--baseline", "-"),
        ]:
            with self.subTest(args=args):
                self.run_cli("", *args, code=1)

    def test_sample_limit_and_random_invalid_bytes(self):
        rng = random.Random(33)
        data = (
            b"".join(bytes([rng.randrange(128, 256)]) + b"\n" for _ in range(1000))
            + row().encode()
        )
        i = self.run_cli(data)["incident"]["input"]
        self.assertEqual(i["rejected"], 1000)
        self.assertEqual(len(i["samples"]), 8)
        self.assertEqual(i["accepted"], 1)

    def test_input_open_failure(self):
        self.run_cli(
            "", "--baseline", str(ROOT / "build" / "does-not-exist.tsv"), code=2
        )

    def test_text_report_is_useful(self):
        p = subprocess.run(
            [str(BIN), "analyze", "-"],
            input=row(status=503) * 20,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=15,
        )
        self.assertEqual(p.returncode, 0)
        self.assertIn("LogScope", p.stdout)
        self.assertIn("/a", p.stdout)
        self.assertIn("ПРЕДУПРЕЖДЕНИЕ", p.stdout)


if __name__ == "__main__":
    unittest.main()


class Boundaries(unittest.TestCase):
    run_cli = CLI.run_cli

    def test_line_limit_and_buffer_boundaries(self):
        for length in [8191, 8192, 8193, 65535, 65536, 65537]:
            with self.subTest(length=length):
                d = self.run_cli(b"x" * length + b"\r\n" + row().encode())
                self.assertEqual(d["incident"]["input"]["accepted"], 1)
                expected = "columns" if length <= 8192 else "line_too_long"
                self.assertEqual(d["incident"]["input"]["reasons"], {expected: 1})
        prefix = b"x" * 65535 + b"\n"
        d = self.run_cli(prefix + row().encode())
        self.assertEqual(d["incident"]["summary"]["requests"], 1)

    def test_baseline_uses_the_same_filters(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            path = Path(td) / "base.tsv"
            path.write_text(row(method="GET") * 9 + row(method="POST", status=503))
            d = self.run_cli(
                row(method="POST", status=503),
                "--baseline",
                str(path),
                "--method",
                "POST",
            )
            self.assertEqual(d["baseline"]["summary"]["requests"], 1)
            self.assertEqual(d["baseline"]["input"]["filtered"], 9)
            self.assertEqual(d["comparison"]["request_ratio"], 1)

    def test_strict_rejection_does_not_read_another_block_from_open_stdin(self):
        p = subprocess.Popen(
            [str(BIN), "analyze", "-", "--json", "--strict"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            p.stdin.write(b"bad\n" + b"x" * (65536 - 4))
            p.stdin.flush()
            try:
                code = p.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.fail(
                    "После первой ошибки процесс повторно ждёт данные из открытого stdin"
                )
            self.assertEqual(code, 3)
        finally:
            p.stdin.close()
            p.stdin = None
            out, err = p.communicate(timeout=5)
        self.assertEqual(out, b"")
        self.assertIn("строка 1".encode(), err)

    def test_alert_exact_baseline_boundary_survives_rounding(self):
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            baseline = Path(td) / "baseline.tsv"
            baseline.write_text(row(status=503) + row() * 6)
            d = self.run_cli(
                row(status=503) * 9 + row() * 12, "--baseline", str(baseline)
            )
            self.assertEqual(len(d["alerts"]), 1)
            d = self.run_cli(
                row(status=503) * 8 + row() * 13, "--baseline", str(baseline)
            )
            self.assertEqual(d["alerts"], [])

    def test_bare_carriage_return_at_eof_is_rejected(self):
        data = row().rstrip("\n") + "\r"
        self.run_cli(data, "--strict", code=3)
        result = self.run_cli(data)
        self.assertEqual(result["incident"]["input"]["rejected"], 1)
        self.assertEqual(result["incident"]["summary"]["requests"], 0)

    def test_decimal_alert_threshold_and_invalid_grammar(self):
        self.assertEqual(
            len(self.run_cli(row(status=503) * 20, "--alert-rate", "99.99")["alerts"]),
            1,
        )
        for value in [
            "nan",
            "inf",
            "-1",
            "+1",
            "1e1",
            "10.",
            ".1",
            "10,5",
            " 1",
            "100.01",
        ]:
            self.run_cli("", "--alert-rate", value, code=1)

    @unittest.skipUnless(
        sys.platform.startswith("linux"),
        "Устройство /dev/full проверяется только на Linux",
    )
    def test_output_failure_on_full_device(self):
        with open("/dev/full", "wb") as sink:
            p = subprocess.run(
                [str(BIN), "analyze", "-", "--json"],
                input=row().encode(),
                stdout=sink,
                stderr=subprocess.PIPE,
            )
        self.assertEqual(p.returncode, 2)

    def test_directory_input_is_fatal_without_report(self):
        p = subprocess.run([str(BIN), "analyze", str(ROOT)], capture_output=True)
        self.assertEqual(p.returncode, 2)
        self.assertEqual(p.stdout, b"")


if __name__ == "__main__":
    unittest.main()
