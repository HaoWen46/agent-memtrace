"""Measurer overhead on a target process: page-touch throughput and faults under three conditions.

none   : nobody reads the target
read   : smaps_rollup + io + stat read at 1 Hz (the sampler's per-process work)
clear  : read at 1 Hz and clear_refs every second (worst case; real phase transitions are rarer)
Writes raw/bench/overhead.csv (one row per repetition) and raw/bench/latency.csv (per operation).
"""
import csv
import os
import subprocess
import sys
import time

from memtrace import procfs

TARGET = r"""
import numpy as np, sys, time
a = np.zeros(1 << 30, dtype=np.uint8); a[::4096] = 1          # fault in 1 GiB
hot = a[: 1 << 28]                                              # sweep the first 256 MiB
print("ready", flush=True); sys.stdin.readline()
t_end = time.time() + float(sys.argv[1]); n = 0
while time.time() < t_end:
    hot[::4096] += 1; n += 1
print(n, flush=True); sys.stdin.readline()                     # stay alive until final stats are read
"""


def run(cond, dur, lat):
    p = subprocess.Popen([sys.executable, "-c", TARGET, str(dur)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "ready"
    f0 = procfs.stat(p.pid)
    p.stdin.write("go\n")
    p.stdin.flush()
    t_end = time.time() + dur
    while time.time() < t_end - 1.0:
        time.sleep(1.0)
        if cond in ("read", "clear"):
            t0 = time.perf_counter()
            procfs.stat(p.pid), procfs.smaps_rollup(p.pid), procfs.io(p.pid)
            lat.writerow({"cond": cond, "op": "read", "ms": f"{(time.perf_counter() - t0) * 1e3:.3f}"})
        if cond == "clear":
            t0 = time.perf_counter()
            procfs.clear_refs(p.pid)
            lat.writerow({"cond": cond, "op": "clear_refs", "ms": f"{(time.perf_counter() - t0) * 1e3:.3f}"})
    sweeps = int(p.stdout.readline())
    f1 = procfs.stat(p.pid)
    p.stdin.write("bye\n")
    p.stdin.flush()
    p.wait()
    return {"cond": cond, "dur_s": dur, "sweeps": sweeps, "pages_per_s": f"{sweeps * 65536 / dur:.0f}",
            "minflt": f1["minflt"] - f0["minflt"], "majflt": f1["majflt"] - f0["majflt"],
            "cpu_s": f"{(f1['cpu_ticks'] - f0['cpu_ticks']) / procfs.CLK_TCK:.2f}"}


def main():
    out = sys.argv[1] if len(sys.argv) > 1 else "raw/bench"
    os.makedirs(out, exist_ok=True)
    with open(f"{out}/overhead.csv", "w", newline="") as f1, open(f"{out}/latency.csv", "w", newline="") as f2:
        w = csv.DictWriter(f1, ["rep", "cond", "dur_s", "sweeps", "pages_per_s", "minflt", "majflt", "cpu_s"])
        lat = csv.DictWriter(f2, ["cond", "op", "ms"])
        w.writeheader()
        lat.writeheader()
        for rep in range(5):
            for cond in ("none", "read", "clear"):
                w.writerow({"rep": rep, **run(cond, 20.0, lat)})
                f1.flush()


if __name__ == "__main__":
    main()
