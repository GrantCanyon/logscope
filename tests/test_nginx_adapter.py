"""Контракты профиля Nginx: точная арифметика, ограниченный поток и честные ошибки."""

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
PROFILE = {
    "msec": "1767225600.123",
    "request_method": "GET",
    "request_uri": "/safe?token=PRIVATE_SENTINEL",
    "status": "200",
    "body_bytes_sent": "42",
    "request_time": "0.123",
}
EXPECTED = b"1767225600123\tGET\t/safe\t200\t42\t123000\n"


def encoded(**changes):
    return json.dumps(
        {**PROFILE, **changes}, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")


class NginxAdapter(unittest.TestCase):
    def module(self):
        self.assertTrue(
            (SCRIPTS / "nginx_to_tsv.py").is_file(), "Адаптер ещё не реализован"
        )
        return importlib.import_module("nginx_to_tsv")

    def invoke(self, data, *args):
        self.module()
        return subprocess.run(
            [sys.executable, str(SCRIPTS / "nginx_to_tsv.py"), "-", *args],
            input=data,
            capture_output=True,
            timeout=10,
        )

    def test_exact_uint64_and_millisecond_precision(self):
        adapter = self.module()
        self.assertEqual(
            adapter.parse_line(encoded()).rstrip("\n"), EXPECTED.decode().rstrip("\n")
        )
        result = adapter.parse_line(
            encoded(
                msec="18446744073709551.615",
                request_time="86400.000",
                body_bytes_sent="18446744073709551615",
            )
        )
        parts = result.split()
        self.assertEqual(
            (parts[0], parts[4], parts[5]),
            ("18446744073709551615", "18446744073709551615", "86400000000"),
        )
        self.assertEqual(
            adapter.parse_line(encoded(msec="0.001", request_time="0.001")).split()[5],
            "1000",
        )
        self.assertEqual(
            adapter.parse_line(encoded(msec="0.000", request_time="0.000")).split()[0],
            "0",
        )
        self.assertIn(
            "?token=PRIVATE_SENTINEL", adapter.parse_line(encoded(), keep_query=True)
        )

    def test_invalid_profile_never_becomes_zero(self):
        adapter = self.module()
        bad = [
            {"msec": "18446744073709551.616"},
            {"msec": "1.00"},
            {"msec": "1.0000"},
            {"msec": "01.000"},
            {"msec": "+1.000"},
            {"msec": "-0.001"},
            {"msec": "1e3"},
            {"request_time": "86400.001"},
            {"request_time": "-"},
            {"request_time": "0.0001"},
            {"body_bytes_sent": "18446744073709551616"},
            {"body_bytes_sent": "9" * 5000},
            {"body_bytes_sent": "00"},
            {"body_bytes_sent": "+0"},
            {"body_bytes_sent": " 0"},
            {"status": "99"},
            {"status": "600"},
            {"status": 200},
            {"status": None},
            {"status": True},
            {"request_method": "get"},
            {"request_method": "A" * 17},
            {"extra": "field"},
        ]
        for changes in bad:
            with self.subTest(changes=list(changes)):
                with self.assertRaises(adapter.AdapterError):
                    adapter.parse_line(encoded(**changes))
        missing = dict(PROFILE)
        del missing["status"]
        for value in [
            json.dumps(missing).encode(),
            encoded()[:-1] + b',"status":"503"}',
            b"[]",
            b"null",
            b"[" * 2000 + b"0" + b"]" * 2000,
            b"\xff",
            b"NaN",
        ]:
            with self.assertRaises(adapter.AdapterError):
                adapter.parse_line(value)

    def test_full_uri_validated_before_query_is_removed(self):
        adapter = self.module()
        for uri in [
            "/a?x=" + c for c in ["\x00", "\t", "\r", "\n", "\x7f", "é", "\ud800"]
        ] + ["*abc", "*?x", "/" + "a" * 2048, "relative", ""]:
            with self.subTest(uri=ascii(uri)):
                with self.assertRaises(adapter.AdapterError):
                    adapter.parse_line(encoded(request_uri=uri))
        self.assertEqual(adapter.parse_line(encoded(request_uri="*")).split()[2], "*")
        self.assertEqual(
            adapter.parse_line(encoded(request_uri="/literal%0A")).split()[2],
            "/literal%0A",
        )
        self.assertEqual(
            len(adapter.parse_line(encoded(request_uri="/" + "a" * 2047)).split()[2]),
            2048,
        )

    def test_strict_and_skip_invalid_keep_diagnostics_private(self):
        data = (
            encoded()
            + b"\n"
            + b'{"request_uri":"/PRIVATE_SENTINEL"}\n'
            + encoded()
            + b"\n"
        )
        result = self.invoke(data)
        self.assertEqual((result.returncode, result.stdout), (3, EXPECTED))
        result = self.invoke(data, "--skip-invalid")
        self.assertEqual((result.returncode, result.stdout), (3, EXPECTED * 2))
        self.assertNotIn(b"PRIVATE_SENTINEL", result.stderr)
        self.assertNotIn(b"Traceback", result.stderr)
        result = self.invoke((b"PRIVATE_SENTINEL\n" * 1000), "--skip-invalid")
        self.assertEqual(result.returncode, 3)
        self.assertLess(len(result.stderr), 2000)
        self.assertNotIn(b"PRIVATE_SENTINEL", result.stderr)

    def test_bounded_line_reader_and_endings(self):
        good = encoded()
        padded = good + b" " * (16384 - len(good))
        for data in [
            good,
            good + b"\n",
            good + b"\r\n",
            padded + b"\n",
            padded + b"\r\n",
            b"",
        ]:
            self.assertEqual(self.invoke(data).returncode, 0)
        for data in [good + b"\r", padded + b" \n"]:
            self.assertEqual(self.invoke(data).returncode, 3)
        result = self.invoke(b"x" * 500000 + b"\n" + good + b"\n", "--skip-invalid")
        self.assertEqual((result.returncode, result.stdout), (3, EXPECTED))

    def test_strict_returns_without_waiting_for_stdin_eof(self):
        self.module()
        p = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "nginx_to_tsv.py"), "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        try:
            p.stdin.write(b"bad-json\n")
            p.stdin.flush()
            self.assertEqual(p.wait(timeout=3), 3)
        finally:
            if p.poll() is None:
                p.kill()
            p.stdin.close()
            p.stdin = None
            _, error = p.communicate(timeout=5)
        self.assertNotIn(b"Traceback", error)

    def test_broken_pipe_is_exactly_io_exit_not_interpreter_120(self):
        self.module()
        p = subprocess.Popen(
            [sys.executable, str(SCRIPTS / "nginx_to_tsv.py"), "-"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        p.stdout.close()
        p.stdout = None
        _, error = p.communicate((encoded() + b"\n") * 300, timeout=10)
        self.assertEqual(p.returncode, 2, error)
        self.assertNotIn(b"Traceback", error)
        self.assertNotIn(b"Exception ignored", error)
        self.assertNotIn(b"PRIVATE_SENTINEL", error)

    def test_closed_diagnostics_pipe_preserves_io_exit(self):
        self.module()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            missing = Path(td) / "PRIVATE_SENTINEL.jsonl"
            p = subprocess.Popen(
                [sys.executable, str(SCRIPTS / "nginx_to_tsv.py"), str(missing)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            p.stderr.close()
            p.stderr = None
            output, _ = p.communicate(timeout=10)
            self.assertEqual(p.returncode, 2)
            self.assertEqual(output, b"")

    def test_file_errors_usage_and_real_core_pipeline_statuses(self):
        self.module()
        with tempfile.TemporaryDirectory(dir=ROOT / "build") as td:
            source = Path(td) / "profile.jsonl"
            source.write_bytes(encoded() + b"\n")
            p = subprocess.run(
                [sys.executable, str(SCRIPTS / "nginx_to_tsv.py"), str(source)],
                capture_output=True,
            )
            self.assertEqual((p.returncode, p.stdout), (0, EXPECTED))
            missing = Path(td) / "PRIVATE_SENTINEL.jsonl"
            p = subprocess.run(
                [sys.executable, str(SCRIPTS / "nginx_to_tsv.py"), str(missing)],
                capture_output=True,
            )
            self.assertEqual(p.returncode, 2)
            self.assertNotIn(b"PRIVATE_SENTINEL", p.stderr)
        p = self.invoke(b"", "--PRIVATE_SENTINEL")
        self.assertEqual(p.returncode, 1)
        self.assertNotIn(b"PRIVATE_SENTINEL", p.stderr)
        converted = self.invoke(encoded() + b"\n")
        core = subprocess.run(
            [str(BIN), "analyze", "-", "--strict", "--json"],
            input=converted.stdout,
            capture_output=True,
        )
        self.assertEqual(core.returncode, 0)
        summary = json.loads(core.stdout)["incident"]["summary"]
        self.assertEqual(
            (summary["requests"], summary["bytes"], summary["latency_us"]["sum"]),
            (1, 42, 123000),
        )
        # Последняя команда способна успешно разобрать префикс: код адаптера нельзя терять.
        converted = self.invoke(encoded() + b"\nbad-json\n")
        core = subprocess.run(
            [str(BIN), "analyze", "-", "--strict", "--json"],
            input=converted.stdout,
            capture_output=True,
        )
        self.assertEqual((converted.returncode, core.returncode), (3, 0))
        converted = self.invoke(
            encoded(body_bytes_sent="18446744073709551615") + b"\n" + encoded() + b"\n"
        )
        core = subprocess.run(
            [str(BIN), "analyze", "-", "--strict", "--json"],
            input=converted.stdout,
            capture_output=True,
        )
        self.assertEqual((converted.returncode, core.returncode), (0, 3))


if __name__ == "__main__":
    unittest.main()
