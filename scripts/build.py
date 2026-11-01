"""Build the actual C sources; no third-party Python packages required."""
import argparse
import os
from pathlib import Path
import shlex
import subprocess

ROOT = Path(__file__).resolve().parents[1]
p = argparse.ArgumentParser()
p.add_argument("--cc", default=os.environ.get("CC", "cc"), help='compiler command, e.g. "zig cc"')
p.add_argument("--sanitize", action="store_true")
p.add_argument("--debug", action="store_true")
p.add_argument("--unit", action="store_true")
a = p.parse_args()
compiler = shlex.split(a.cc, posix=os.name != "nt")
build = ROOT / "build"
build.mkdir(exist_ok=True)
flags = ["-std=c17", "-Wall", "-Wextra", "-Wpedantic", "-Wconversion", "-Wshadow", "-Werror", "-I", str(ROOT / "src")]
flags += ["-O0", "-g"] if a.debug or a.sanitize else ["-O2"]
if a.sanitize:
    flags += ["-fsanitize=address,undefined", "-fno-omit-frame-pointer"]
core = [ROOT / "src" / (n + ".c") for n in ["parse", "stats", "top", "window"]]
sources = core + ([ROOT / "tests" / "unit.c"] if a.unit else [ROOT / "src" / (n + ".c") for n in ["reader", "report", "main"]])
out = build / (("unit" if a.unit else "logscope") + (".exe" if os.name == "nt" else ""))
subprocess.run(compiler + flags + [str(x) for x in sources] + ["-o", str(out)], check=True, cwd=ROOT)
print(out)
