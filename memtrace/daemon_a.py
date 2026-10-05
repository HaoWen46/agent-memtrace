"""Workload A: monitor every Claude Code / Codex process tree of this user on this host.

Session logs are read only for timestamps and record types; no message content is stored.
"""
import argparse
import glob
import json
import os
import signal
import time
from datetime import datetime

from . import procfs
from .sampler import HOST, Sampler, Source, Tree

HOME = os.path.expanduser("~")
CODEX_DIRS = [f"{HOME}/.codex/sessions", f"/tmp2/{os.environ.get('USER', '')}/.sys/.codex/sessions"]
WARM = 4 << 20
with open("/proc/stat") as _f:
    BTIME = next(int(l.split()[1]) for l in _f if l.startswith("btime"))


def my_pids():
    uid = os.getuid()
    for d in os.listdir("/proc"):
        if d.isdigit():
            try:
                if os.stat(f"/proc/{d}").st_uid == uid:
                    yield int(d)
            except OSError:
                pass


def agent_kind(pid):
    try:
        with open(f"/proc/{pid}/comm") as f:
            comm = f.read().strip()
    except OSError:
        return None
    return "claude" if comm == "claude" else "codex" if comm.startswith("codex") else None


def claude_session(pid):
    try:
        with open(f"{HOME}/.claude/sessions/{pid}.json") as f:
            sid = json.load(f).get("sessionId")
    except (OSError, ValueError):
        return None, None
    paths = glob.glob(f"{HOME}/.claude/projects/*/{sid}.jsonl")
    return sid, (paths[0] if paths else None)


def codex_session(pid):
    try:
        for fd in os.listdir(f"/proc/{pid}/fd"):
            try:
                link = os.readlink(f"/proc/{pid}/fd/{fd}")
            except OSError:
                continue
            base = os.path.basename(link)
            if base.startswith("rollout-") and base.endswith(".jsonl"):
                return base[:-6], link
        cwd = os.readlink(f"/proc/{pid}/cwd")
        t_start = BTIME + procfs.stat(pid)["starttime"] / procfs.CLK_TCK
    except OSError:
        return None, None
    cands = [p for d in CODEX_DIRS for p in glob.glob(f"{d}/*/*/*/rollout-*.jsonl") if os.path.getmtime(p) >= t_start]
    for p in sorted(cands, key=os.path.getmtime, reverse=True):
        try:
            with open(p) as f:
                meta = json.loads(f.readline()).get("payload", {})
        except (OSError, ValueError):
            continue
        if meta.get("cwd") == cwd:
            return os.path.basename(p)[:-6], p
    return None, None


def discover(s):
    roots = {p: k for p in my_pids() if (k := agent_kind(p))}
    for pid in list(roots):  # an agent nested inside another agent's tree belongs to that tree
        p, hops = pid, 0
        while hops < 64:
            try:
                p = procfs.stat(p)["ppid"]
            except OSError:
                break
            if p <= 1:
                break
            if p in roots:
                roots.pop(pid)
                break
            hops += 1
    for tid, tree in list(s.trees.items()):
        if not tree.alive():
            s.remove(tid)
    for pid, kind in roots.items():
        try:
            st = procfs.stat(pid)["starttime"]
            cwd = os.readlink(f"/proc/{pid}/cwd")
        except OSError:
            continue
        tid = f"{HOST}-{kind}-{pid}-{st}"
        sid, path = (claude_session if kind == "claude" else codex_session)(pid)
        tree = s.trees.get(tid)
        if tree is None:
            src = Source(path, kind, warm_bytes=WARM) if path else None
            s.add(Tree(tid, kind, pid, st, src, workdirs=[cwd] if cwd.startswith("/tmp2/") else [], meta={"session": sid or ""}))
        elif path and (tree.source is None or tree.source.path != path):
            tree.source = Source(path, kind, warm_bytes=WARM)
            tree.phase = tree.source.warm_phase
            tree.meta["session"] = sid
            s.log_tree("switch", tree)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--until", required=True, help="local time, e.g. 2026-10-08T09:00")
    ap.add_argument("--no-clear", action="store_true")
    a = ap.parse_args()
    until = datetime.fromisoformat(a.until).timestamp()
    s = Sampler(a.out, rotate=True, clear=not a.no_clear)
    signal.signal(signal.SIGTERM, lambda *_: setattr(s, "stop", True))
    signal.signal(signal.SIGINT, lambda *_: setattr(s, "stop", True))
    meta = {"host": HOST, "pid": os.getpid(), "t_start": time.time(), "until": until, "clear": not a.no_clear}
    with open(os.path.join(a.out, f"run-{int(meta['t_start'])}.json"), "w") as f:
        json.dump(meta, f)
    s.run(until=until, hook=discover, hook_every=5.0)
    meta["t_stop"] = time.time()
    with open(os.path.join(a.out, f"run-{int(meta['t_start'])}.json"), "w") as f:
        json.dump(meta, f)


if __name__ == "__main__":
    main()
