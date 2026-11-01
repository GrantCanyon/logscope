"""Сборка исходников C без сторонних пакетов Python."""

from cli import Parser
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
p = Parser()
p.add_argument(
    "--cc",
    default=os.environ.get("CC", "cc"),
    help='Команда компилятора, например "zig cc"',
)
p.add_argument("--sanitize", action="store_true", help="Включить ASan/UBSan")
p.add_argument("--debug", action="store_true", help="Отладочная сборка без оптимизации")
p.add_argument("--unit", action="store_true", help="Собрать модульные проверки")
a = p.parse_args()
compiler = shlex.split(a.cc, posix=os.name != "nt")
build = ROOT / "build"
build.mkdir(exist_ok=True)
flags = [
    "-std=c17",
    "-Wall",
    "-Wextra",
    "-Wpedantic",
    "-Wconversion",
    "-Wshadow",
    "-Werror",
    "-I",
    str(ROOT / "src"),
]
flags += ["-O0", "-g"] if a.debug or a.sanitize else ["-O2"]
if a.sanitize:
    flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
core = [ROOT / "src" / (n + ".c") for n in ["parse", "stats", "top", "window"]]
sources = core + (
    [ROOT / "tests" / "unit.c"]
    if a.unit
    else [ROOT / "src" / (n + ".c") for n in ["reader", "report", "main"]]
)
out = build / (("unit" if a.unit else "logscope") + (".exe" if os.name == "nt" else ""))
subprocess.run(
    compiler + flags + [str(x) for x in sources] + ["-o", str(out)],
    check=True,
    cwd=ROOT,
)
print(out)
