"""Stdlib-only readers for /proc and cgroup v2 files. Sizes from smaps are in kB."""
import os

CLK_TCK = os.sysconf("SC_CLK_TCK")

_SMAPS = {"Rss": "rss", "Pss": "pss", "Pss_Anon": "pss_anon", "Pss_File": "pss_file",
          "Referenced": "ref", "Anonymous": "anon", "Swap": "swap"}
SMAPS_FIELDS = tuple(_SMAPS.values())
IO_FIELDS = ("rchar", "wchar", "read_bytes", "write_bytes")
STAT_FIELDS = ("ppid", "comm", "cpu_ticks", "minflt", "majflt", "nthreads", "starttime")


def smaps_rollup(pid):
    out = dict.fromkeys(SMAPS_FIELDS, 0)
    with open(f"/proc/{pid}/smaps_rollup") as f:
        for line in f:
            key = _SMAPS.get(line.partition(":")[0])
            if key:
                out[key] = int(line.split()[1])
    return out


def io(pid):
    out = {}
    with open(f"/proc/{pid}/io") as f:
        for line in f:
            k, _, v = line.partition(":")
            out[k] = int(v)
    return {k: out[k] for k in IO_FIELDS}


def stat(pid):
    """cpu_ticks and faults include reaped children (cutime/cstime, cminflt/cmajflt)."""
    with open(f"/proc/{pid}/stat") as f:
        s = f.read()
    rp = s.rindex(")")
    fl = s[rp + 2:].split()  # fl[n-3] is field n of proc(5)
    return {"ppid": int(fl[1]), "comm": s[s.index("(") + 1:rp].replace(",", "_"),
            "cpu_ticks": sum(int(x) for x in fl[11:15]),
            "minflt": int(fl[7]) + int(fl[8]), "majflt": int(fl[9]) + int(fl[10]),
            "nthreads": int(fl[17]), "starttime": int(fl[19])}


def children(pid):
    kids = []
    try:
        tids = os.listdir(f"/proc/{pid}/task")
    except OSError:
        return kids
    for tid in tids:
        try:
            with open(f"/proc/{pid}/task/{tid}/children") as f:
                kids.extend(int(x) for x in f.read().split())
        except OSError:
            pass
    return kids


def descendants(root, exclude=frozenset()):
    """root plus all descendants, in DFS order; pids in exclude prune their subtree."""
    seen, stack = [], [root]
    while stack:
        p = stack.pop()
        if p in exclude or p in seen:
            continue
        seen.append(p)
        stack.extend(children(p))
    return seen


def cgroup_of(pid):
    with open(f"/proc/{pid}/cgroup") as f:
        return "/sys/fs/cgroup" + f.read().strip().split("::", 1)[1]


def cgroup_procs(cg):
    with open(f"{cg}/cgroup.procs") as f:
        return [int(x) for x in f.read().split()]


CG_STAT_KEYS = ("anon", "file", "active_anon", "inactive_anon", "active_file", "inactive_file",
                "shmem", "file_mapped", "kernel")


def cgroup_mem(cg):
    """memory.current/peak plus selected memory.stat, io.stat totals and cpu usage (bytes / usec)."""
    out = {}
    with open(f"{cg}/memory.current") as f:
        out["cg_current"] = int(f.read())
    try:
        with open(f"{cg}/memory.peak") as f:
            out["cg_peak"] = int(f.read())
    except OSError:
        out["cg_peak"] = ""
    with open(f"{cg}/memory.stat") as f:
        for line in f:
            k, v = line.split()
            if k in CG_STAT_KEYS:
                out["cg_" + k] = int(v)
    rb = wb = 0
    try:
        with open(f"{cg}/io.stat") as f:
            for line in f:
                for kv in line.split()[1:]:
                    k, _, v = kv.partition("=")
                    rb += int(v) if k == "rbytes" else 0
                    wb += int(v) if k == "wbytes" else 0
    except OSError:
        pass
    out["cg_rbytes"], out["cg_wbytes"] = rb, wb
    with open(f"{cg}/cpu.stat") as f:
        out["cg_cpu_usec"] = int(f.readline().split()[1])
    return out


CG_FIELDS = ("cg_current", "cg_peak", *("cg_" + k for k in CG_STAT_KEYS), "cg_rbytes", "cg_wbytes", "cg_cpu_usec")


def clear_refs(pid):
    with open(f"/proc/{pid}/clear_refs", "w") as f:
        f.write("1")
