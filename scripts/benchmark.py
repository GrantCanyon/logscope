"""Повторяемый замер реального процесса: время, пропускная способность и peak RSS."""
from cli import Parser
import ctypes
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import statistics
import subprocess
import tempfile
import time
from generate import generate

ROOT = Path(__file__).resolve().parents[1]

def run_process(command, output):
    start = time.perf_counter()
    with output.open("wb") as report, tempfile.TemporaryFile() as errors:
        process = subprocess.Popen(command, stdout=report, stderr=errors)
        if os.name == "nt":
            process.wait()
            class MemoryCounters(ctypes.Structure):
                _fields_ = [("cb",ctypes.c_ulong),("PageFaultCount",ctypes.c_ulong)] + [
                    (name,ctypes.c_size_t) for name in ["PeakWorkingSetSize","WorkingSetSize","QuotaPeakPagedPoolUsage","QuotaPagedPoolUsage","QuotaPeakNonPagedPoolUsage","QuotaNonPagedPoolUsage","PagefileUsage","PeakPagefileUsage"]]
            counters = MemoryCounters()
            counters.cb = ctypes.sizeof(counters)
            get_memory = ctypes.WinDLL("psapi",use_last_error=True).GetProcessMemoryInfo
            get_memory.argtypes = [ctypes.c_void_p,ctypes.POINTER(MemoryCounters),ctypes.c_ulong]
            get_memory.restype = ctypes.c_int
            if not get_memory(int(process._handle),ctypes.byref(counters),counters.cb):
                raise OSError(ctypes.get_last_error(),"Не удалось измерить память процесса")
            rss = counters.PeakWorkingSetSize
        else:
            _, status, usage = os.wait4(process.pid,0)
            process.returncode = os.waitstatus_to_exitcode(status)
            rss = int(usage.ru_maxrss) * (1 if platform.system() == "Darwin" else 1024)
        elapsed = time.perf_counter()-start
        if process.returncode:
            errors.seek(0)
            raise RuntimeError(f"Анализ завершился с кодом {process.returncode}: {errors.read().decode('utf-8',errors='replace')}")
    return elapsed, rss

def main():
    parser = Parser(description="Замерить LogScope на синтетических журналах; генерация не входит во время анализа.")
    parser.add_argument("--binary",type=Path,default=ROOT/"build"/("logscope.exe" if os.name=="nt" else "logscope"),help="Исполняемый файл")
    parser.add_argument("--sizes",default="10000,100000,1000000",help="Размеры выборок через запятую")
    parser.add_argument("--repeats",type=int,default=3,help="Число измерений после прогрева")
    parser.add_argument("--capacity",type=int,default=128,help="Ёмкость top")
    parser.add_argument("--cardinality",type=int,default=10000,help="Число возможных путей")
    parser.add_argument("--output",type=Path,default=ROOT/"build/benchmark.json",help="Результат JSON")
    parser.add_argument("--compiler-description",required=True,help="Компилятор, версия и флаги сборки")
    args=parser.parse_args()
    try:
        sizes=[int(n) for n in args.sizes.split(',')]
    except ValueError:
        parser.error("Размеры должны быть целыми числами")
    if not sizes or min(sizes)<1 or args.repeats<1 or args.cardinality<1 or not 1<=args.capacity<=4096:
        parser.error("Некорректные размеры, повторения или ёмкость")
    binary=args.binary.resolve()
    result={"measured_at_utc":datetime.now(timezone.utc).isoformat(),"synthetic":True,
            "environment":{"os":platform.system(),"os_release":platform.release(),"architecture":platform.machine(),"processor":platform.processor(),"logical_cpus":os.cpu_count(),"python":platform.python_version(),"compiler":args.compiler_description,"binary_sha256":hashlib.sha256(binary.read_bytes()).hexdigest()},
            "method":{"seed":2026,"capacity":args.capacity,"window_minutes":120,"cardinality":args.cardinality,"repeats":args.repeats,"warmup_runs":1,"timing":"wall clock; создание процесса, чтение файла, анализ и JSON; генерация исключена","memory":"Windows PeakWorkingSetSize / Unix wait4 ru_maxrss; отдельный процесс на каждый запуск","cache":"после генерации и одного прогрева; холодный диск не измеряется"},"measurements":[]}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.output.parent,prefix="logscope-benchmark-") as folder:
        data, output = Path(folder)/"input.tsv", Path(folder)/"report.json"
        for size in sizes:
            truth=generate(data,size,2026,True,args.cardinality)
            command=[str(binary),"analyze",str(data),"--json","--capacity",str(args.capacity)]
            runs=[]
            for i in range(args.repeats+1):
                elapsed,rss=run_process(command,output)
                summary=json.loads(output.read_bytes())["incident"]["summary"]
                if any(summary[k]!=truth[k] for k in ["requests","errors_5xx","bytes"]) or summary["latency_us"]["sum"]!=truth["latency_sum_us"]:
                    raise RuntimeError("Контрольные метрики не совпали: замер недействителен")
                if i:
                    runs.append({"seconds":elapsed,"peak_rss_bytes":rss})
            median=statistics.median(r["seconds"] for r in runs)
            result["measurements"].append({"requests":size,"input_bytes":data.stat().st_size,"median_seconds":median,"median_requests_per_second":size/median,"median_mib_per_second":data.stat().st_size/(1024**2)/median,"runs":runs})
    args.output.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n",encoding="utf-8")
    print(f"Замеры сохранены: {args.output}")

if __name__=="__main__":
    main()
