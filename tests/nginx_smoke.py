"""Отдельная CI-проверка настоящего Nginx; не входит в локальный unittest discover."""

import http.client
import json
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from cli import Parser

PROFILE = ROOT / "examples/nginx/logscope-json.conf"
ADAPTER = ROOT / "scripts/nginx_to_tsv.py"
FIELDS = {
    "msec",
    "request_method",
    "request_uri",
    "status",
    "body_bytes_sent",
    "request_time",
}


def command(arguments, **kwargs):
    return subprocess.run(
        arguments, check=True, capture_output=True, timeout=120, **kwargs
    )


def request(port, method, target):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=3)
    try:
        connection.request(method, target)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def main():
    parser = Parser(
        description="Проверить профиль LogScope на настоящем официальном Nginx-контейнере."
    )
    parser.add_argument("--image", default="nginx:stable-alpine")
    parser.add_argument("--binary", type=Path, default=ROOT / "build/logscope")
    parser.add_argument("--output", type=Path, default=ROOT / "build/nginx-smoke")
    args = parser.parse_args()
    folder = args.output.resolve() / ("run-" + uuid.uuid4().hex[:12])
    folder.mkdir(parents=True, exist_ok=False)
    configuration = folder / "nginx.conf"
    configuration.write_text(
        """worker_processes 1;
error_log /dev/stderr notice;
pid /tmp/logscope-nginx.pid;
events { worker_connections 64; }
http {
    include /etc/nginx/logscope-json.conf;
    access_log /workspace/access.jsonl logscope_json_v1;
    server {
        listen 8080;
        server_name localhost;
        location = /ready { access_log off; return 204; }
        location = /ok { return 200 "OK\\n"; }
        location = /fail { return 503 "FAIL\\n"; }
        location = /empty { return 204; }
    }
}
""",
        encoding="utf-8",
    )
    command(["docker", "pull", args.image])
    inspected = json.loads(command(["docker", "image", "inspect", args.image]).stdout)[
        0
    ]
    version = command(["docker", "run", "--rm", args.image, "nginx", "-v"])
    mounts = [
        "--mount",
        f"type=bind,src={folder},dst=/workspace",
        "--mount",
        f"type=bind,src={PROFILE.resolve()},dst=/etc/nginx/logscope-json.conf,readonly",
    ]
    syntax = command(
        [
            "docker",
            "run",
            "--rm",
            *mounts,
            args.image,
            "nginx",
            "-t",
            "-c",
            "/workspace/nginx.conf",
        ]
    )
    (folder / "syntax.txt").write_bytes(syntax.stdout + syntax.stderr)
    name = "logscope-smoke-" + uuid.uuid4().hex[:12]
    started = False
    try:
        command(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                name,
                "--publish",
                "127.0.0.1::8080",
                *mounts,
                args.image,
                "nginx",
                "-g",
                "daemon off;",
                "-c",
                "/workspace/nginx.conf",
            ]
        )
        started = True
        binding = command(["docker", "port", name, "8080/tcp"]).stdout.decode().strip()
        host, port_text = binding.rsplit(":", 1)
        if host != "127.0.0.1":
            raise RuntimeError("Контейнер опубликован не на loopback")
        port = int(port_text)
        for attempt in range(50):
            try:
                if request(port, "GET", "/ready")[0] == 204:
                    break
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.1)
        else:
            raise RuntimeError("Nginx не стал готов за отведённое время")
        samples = [
            ("GET", "/ok?token=SYNTHETIC_SECRET", 200, b"OK\n"),
            ("POST", "/ok", 200, b"OK\n"),
            ("GET", "/fail", 503, b"FAIL\n"),
            ("GET", "/empty", 204, b""),
            ("GET", "/ok?second=1", 200, b"OK\n"),
            ("HEAD", "/ok", 200, b""),
        ]
        for method, target, status, body in samples:
            assert request(port, method, target) == (status, body)
        command(
            [
                "docker",
                "exec",
                name,
                "nginx",
                "-s",
                "quit",
                "-c",
                "/workspace/nginx.conf",
            ]
        )
        assert command(["docker", "wait", name]).stdout.strip() == b"0"
        records = [
            json.loads(line)
            for line in (folder / "access.jsonl")
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        assert len(records) == len(samples)
        for record in records:
            assert set(record) == FIELDS and all(
                type(value) is str for value in record.values()
            )
            assert re.fullmatch(r"(0|[1-9][0-9]*)\.[0-9]{3}", record["msec"])
            assert re.fullmatch(r"(0|[1-9][0-9]*)\.[0-9]{3}", record["request_time"])
        converted = command(
            [sys.executable, str(ADAPTER), str(folder / "access.jsonl")]
        )
        assert (
            b"?" not in converted.stdout
            and b"SYNTHETIC_SECRET" not in converted.stdout + converted.stderr
        )
        (folder / "converted.tsv").write_bytes(converted.stdout)
        analyzed = command(
            [str(args.binary.resolve()), "analyze", "-", "--strict", "--json"],
            input=converted.stdout,
        )
        (folder / "report.json").write_bytes(analyzed.stdout)
        report = json.loads(analyzed.stdout)["incident"]
        assert report["summary"]["requests"] == 6
        assert report["summary"]["errors_5xx"] == 1
        assert report["summary"]["status_codes"] == {"200": 4, "204": 1, "503": 1}
        assert (
            report["summary"]["bytes"]
            == sum(len(sample[3]) for sample in samples)
            == 14
        )
        expected_latency = sum(
            int(record["request_time"].replace(".", "")) * 1000 for record in records
        )
        assert report["summary"]["latency_us"]["sum"] == expected_latency
        assert report["input"]["rejected"] == 0
        assert all(
            int(line.split(b"\t")[5]) % 1000 == 0
            for line in converted.stdout.splitlines()
        )
        assert len(report["top_requests"]) == 5
        metadata = {
            "image_requested": args.image,
            "image_id": inspected["Id"],
            "repo_digests": inspected.get("RepoDigests", []),
            "nginx_version": (version.stdout + version.stderr)
            .decode("utf-8", errors="replace")
            .strip(),
            "synthetic_requests": 6,
            "adapter_exit": converted.returncode,
            "core_exit": analyzed.returncode,
            "result": "ПРОЙДЕНО",
        }
        (folder / "verification.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        print(
            "Проверено: nginx -t, 6 синтетических HTTP-запросов, настоящий журнал, адаптер и C CLI."
        )
    finally:
        if started:
            subprocess.run(
                ["docker", "rm", "--force", name], capture_output=True, timeout=30
            )
    return 0


if __name__ == "__main__":
    sys.exit(main())
