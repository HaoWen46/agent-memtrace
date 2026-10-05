"""1 Hz process-tree memory sampler with phase-triggered clear_refs.

Event sources are polled every POLL seconds; each phase transition clears Referenced bits of every
tree member, so Referenced at a later sample = pages touched since that phase began. Primary samples
fall on whole wall-clock seconds; trees in live tool_exec also get burst samples every BURST seconds
(flagged primary=0). All timestamps are time.time() on this host.
"""
import csv
import json
import math
import os
import re
import resource
import socket
import subprocess
import threading
import time

from . import procfs
from .phases import PHASERS

POLL = 0.05
BURST = 0.1
MINCORE_PERIOD = 10.0
CLEAR_PHASES = ("model_wait", "tool_exec", "user_wait")
AGG = (*procfs.SMAPS_FIELDS, *procfs.IO_FIELDS, "cpu_ticks", "minflt", "majflt", "nthreads")
HOST = socket.gethostname()


def self_rss_kb():
    with open("/proc/self/statm") as f:
        return int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") // 1024


class CSVOut:
    """Append-only CSV with header; optional hourly rotation (name-YYYYmmddHH.csv)."""

    def __init__(self, outdir, name, fields, rotate=False):
        self.outdir, self.name, self.fields, self.rotate = outdir, name, fields, rotate
        self.f = self.w = self.suffix = None

    def write(self, row):
        suffix = time.strftime("-%Y%m%d%H") if self.rotate else ""
        if suffix != self.suffix:
            if self.f:
                self.f.close()
            path = os.path.join(self.outdir, f"{self.name}{suffix}.csv")
            new = not os.path.exists(path)
            self.f = open(path, "a", newline="", buffering=1)
            self.w = csv.DictWriter(self.f, self.fields, extrasaction="ignore")
            if new:
                self.w.writeheader()
            self.suffix = suffix
        self.w.writerow(row)

    def close(self):
        if self.f:
            self.f.close()


class Source:
    """Incrementally tails a session/event JSONL file through a phaser."""

    def __init__(self, path, kind, warm_bytes=None, on_record=None):
        self.path, self.kind, self.on_record = path, kind, on_record
        self.ph = PHASERS[kind]()
        self.buf = b""
        self.pos = 0
        if warm_bytes is not None:  # attach mid-session: replay the tail silently to learn the phase
            size = os.path.getsize(path)
            self.pos = max(0, size - warm_bytes)
            for _ in self._read(skip_first=self.pos > 0):
                pass
            self.warm_phase = self.ph.phase

    def _read(self, skip_first=False):
        try:
            with open(self.path, "rb") as f:
                f.seek(self.pos)
                data = f.read()
        except OSError:
            return
        self.pos += len(data)
        lines = (self.buf + data).split(b"\n")
        self.buf = lines.pop()
        if skip_first and lines:
            lines.pop(0)
        for line in lines:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if self.on_record:
                self.on_record(d)
            yield from self.ph.feed(d)

    def poll(self):
        try:
            if os.path.getsize(self.path) == self.pos:
                return []
        except OSError:
            return []
        return list(self._read())


class Tree:
    """A monitored process tree. members() returns [(pid, role)].

    A: root = claude/codex process, role 'root' for it and 'desc' for descendants.
    B: root = agent_b harness ('harness' + descendants), plus every pid in the container cgroup ('sandbox').
    """

    def __init__(self, tid, kind, root, starttime, source=None, workdirs=(), meta=None):
        self.tid, self.kind, self.root, self.starttime = tid, kind, root, starttime
        self.source, self.workdirs, self.meta = source, list(workdirs), meta or {}
        self.sandbox_cg = None
        self.phase = getattr(source, "warm_phase", None)
        self.last_clear = ""

    def alive(self):
        try:
            return procfs.stat(self.root)["starttime"] == self.starttime
        except OSError:
            return False

    def members(self, exclude):
        roles = "harness" if self.kind == "agent_b" else None
        out = [(p, roles or ("root" if p == self.root else "desc")) for p in procfs.descendants(self.root, exclude)]
        if self.sandbox_cg:
            try:
                out += [(p, "sandbox") for p in procfs.cgroup_procs(self.sandbox_cg)]
            except OSError:
                pass
        return out


class Sampler:
    def __init__(self, outdir, rotate=False, clear=True):
        os.makedirs(outdir, exist_ok=True)
        self.outdir, self.clear = outdir, clear
        self.trees = {}
        self.exclude = {os.getpid()}
        self.stop = False
        self.n_clears = 0
        self.mc_busy = False
        self.mc_next = {}
        S = ("t", "tree", "role", "primary", "live_phase", "last_clear", "n", *AGG)
        self.samples = CSVOut(outdir, "samples", S + procfs.CG_FIELDS, rotate)
        self.procs = CSVOut(outdir, "procs", ("t", "tree", "role", "pid", "ppid", "comm", "starttime", *AGG), rotate)
        self.events = CSVOut(outdir, "events", ("t_detect", "tree", "t_record", "phase", "cause", "tools", "step",
                                                "clear_start", "clear_end", "n_cleared", "clear_err"), rotate)
        self.mincore = CSVOut(outdir, "mincore", ("t", "dir", "trees", "files", "resident_pages", "total_pages", "elapsed", "status"), rotate)
        self.overhead = CSVOut(outdir, "overhead", ("t", "sample_ms", "n_trees", "n_procs", "self_cpu_s", "self_rss_kb",
                                                    "children_cpu_s", "n_clears"), rotate)
        self.treelog = CSVOut(outdir, "trees", ("t", "event", "tree", "kind", "root", "starttime", "session", "path", "workdirs"), rotate)

    # ---- tree bookkeeping
    def add(self, tree):
        self.trees[tree.tid] = tree
        self.log_tree("attach", tree)

    def remove(self, tid):
        tree = self.trees.pop(tid, None)
        if tree:
            self.log_tree("detach", tree)

    def log_tree(self, ev, tree):
        self.treelog.write({"t": f"{time.time():.3f}", "event": ev, "tree": tree.tid, "kind": tree.kind, "root": tree.root,
                            "starttime": tree.starttime, "session": tree.meta.get("session", ""),
                            "path": tree.source.path if tree.source else "", "workdirs": "|".join(tree.workdirs)})

    # ---- phase transitions
    def on_transition(self, tree, tr):
        row = {"t_detect": f"{time.time():.3f}", "tree": tree.tid, "t_record": f"{tr['t']:.3f}", "phase": tr["phase"],
               "cause": tr["cause"], "tools": tr.get("tools", ""), "step": tr.get("step", "")}
        tree.phase = tr["phase"]
        if self.clear and tr["phase"] in CLEAR_PHASES:
            t0, n, err = time.time(), 0, 0
            for pid, _ in tree.members(self.exclude):
                try:
                    procfs.clear_refs(pid)
                    n += 1
                except OSError:
                    err += 1
            t1 = time.time()
            tree.last_clear = f"{t1:.3f}"
            self.n_clears += 1
            row.update(clear_start=f"{t0:.3f}", clear_end=f"{t1:.3f}", n_cleared=n, clear_err=err)
        self.events.write(row)

    # ---- sampling
    def sample(self, trees, primary):
        t = time.time()
        nprocs = 0
        for tree in trees:
            agg = {}
            for pid, role in tree.members(self.exclude):
                try:
                    st = procfs.stat(pid)
                    rec = {**st, **procfs.smaps_rollup(pid), **procfs.io(pid)}
                except (OSError, ValueError, IndexError):
                    continue  # exited mid-read
                nprocs += 1
                a = agg.setdefault(role, dict.fromkeys(AGG, 0) | {"n": 0})
                a["n"] += 1
                for k in AGG:
                    a[k] += rec[k]
                if primary:
                    self.procs.write({"t": f"{t:.3f}", "tree": tree.tid, "role": role, "pid": pid, **rec})
            if tree.sandbox_cg and "sandbox" in agg:
                try:
                    agg["sandbox"].update(procfs.cgroup_mem(tree.sandbox_cg))
                except OSError:
                    pass
            for role, a in agg.items():
                self.samples.write({"t": f"{t:.3f}", "tree": tree.tid, "role": role, "primary": int(primary),
                                    "live_phase": tree.phase or "", "last_clear": tree.last_clear, **a})
        return t, nprocs

    # ---- page-cache residency of work dirs (vmtouch = mmap + mincore; does not touch pages).
    # Each dir is scanned once per cycle even if several trees share it; a dir whose scan takes
    # e seconds is rescanned every max(MINCORE_PERIOD, 20 e) seconds to bound vmtouch CPU at ~5%.
    def mincore_job(self, jobs):
        try:
            for d, tids in jobs:
                t0 = time.time()
                try:
                    r = subprocess.run(["vmtouch", "-F", d], capture_output=True, text=True, timeout=30)
                    m = re.search(r"Files: (\d+).*Resident Pages: (\d+)/(\d+)", r.stdout, re.S)
                    row = {"files": m[1], "resident_pages": m[2], "total_pages": m[3], "status": "ok"} if m else {"status": "parse"}
                except subprocess.TimeoutExpired:
                    row = {"status": "timeout"}
                el = time.time() - t0
                self.mc_next[d] = t0 + max(MINCORE_PERIOD, 20 * el)
                self.mincore.write({"t": f"{t0:.3f}", "dir": d, "trees": "|".join(tids), "elapsed": f"{el:.3f}", **row})
        finally:
            self.mc_busy = False

    def maybe_mincore(self, now):
        if self.mc_busy:
            return
        dirs = {}
        for tree in self.trees.values():
            for d in tree.workdirs:
                dirs.setdefault(d, []).append(tree.tid)
        jobs = [(d, tids) for d, tids in dirs.items() if now >= self.mc_next.get(d, 0.0)]
        if jobs:
            self.mc_busy = True
            threading.Thread(target=self.mincore_job, args=(jobs,), daemon=True).start()

    # ---- main loop
    def run(self, until=math.inf, hook=None, hook_every=5.0):
        next_tick = math.floor(time.time()) + 1
        next_burst = 0.0
        next_hook = 0.0
        while not self.stop and time.time() < until:
            for tree in list(self.trees.values()):
                if tree.source:
                    for tr in tree.source.poll():
                        self.on_transition(tree, tr)
            now = time.time()
            if hook and now >= next_hook:
                hook(self)
                next_hook = now + hook_every
            if now >= next_tick:
                t, n = self.sample(list(self.trees.values()), True)
                ms = (time.time() - t) * 1000
                ru, rc = resource.getrusage(resource.RUSAGE_SELF), resource.getrusage(resource.RUSAGE_CHILDREN)
                self.overhead.write({"t": f"{t:.3f}", "sample_ms": f"{ms:.2f}", "n_trees": len(self.trees), "n_procs": n,
                                     "self_cpu_s": f"{ru.ru_utime + ru.ru_stime:.3f}", "self_rss_kb": self_rss_kb(),
                                     "children_cpu_s": f"{rc.ru_utime + rc.ru_stime:.3f}", "n_clears": self.n_clears})
                self.maybe_mincore(t)
                next_tick = math.floor(time.time()) + 1  # a sample overrunning a second skips that tick
                next_burst = time.time() + BURST
            elif now >= next_burst:
                hot = [tr for tr in self.trees.values() if tr.phase == "tool_exec"]
                if hot:
                    self.sample(hot, False)
                next_burst = now + BURST
            time.sleep(max(0.0, min(POLL, next_tick - time.time())))
        for out in (self.samples, self.procs, self.events, self.mincore, self.overhead, self.treelog):
            out.close()
