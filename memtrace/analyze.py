"""Recompute every reported number and figure from raw/.

Outputs: numbers.json (all report numbers), results/*.csv (per-interval / per-task tables),
report/fig/*.pdf. Statistics follow report/prereg.typ; anything not pre-registered is marked
exploratory in numbers.json.
"""
import glob
import hashlib
import json
import subprocess
import time
from pathlib import Path

import matplotlib
import numpy as np
import pandas as pd

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402

from .phases import intervals, transitions_from_file  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
RAW, RES, FIG = ROOT / "raw", ROOT / "results", ROOT / "report" / "fig"
KB = 1024
GIB = 1 << 30
PH_COLOR = {"model_wait": "#2a78d6", "tool_exec": "#eb6834", "user_wait": "#1baf7a"}
OTHER = "#898781"
INK, INK2, MUTED, SURF = "#0b0b0b", "#52514e", "#898781", "#fcfcfb"
LEN_GROUPS = [("<5 s", 0, 5, "#86b6ef"), ("5–30 s", 5, 30, "#2a78d6"), (">30 s", 30, np.inf, "#104281")]
SUM_COLS = ["n", "rss", "pss", "ref", "anon", "swap", "rchar", "wchar", "read_bytes", "write_bytes", "cpu_ticks",
            "cg_current", "cg_anon", "cg_file", "cg_rbytes", "cg_wbytes"]
CUM = ["rchar", "wchar", "read_bytes", "write_bytes", "cg_rbytes", "cg_wbytes"]


def k3(x):
    return f"{x:.3f}"


def load(pattern):
    frames = []
    for f in sorted(glob.glob(str(pattern))):
        try:
            frames.append(pd.read_csv(f, low_memory=False))
        except pd.errors.EmptyDataError:
            pass
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def q(x, p):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return float(np.percentile(x, p)) if len(x) else None


def stats(x):
    x = np.asarray(x, float)
    x = x[~np.isnan(x)]
    return {"n": int(len(x)), "median": q(x, 50), "p95": q(x, 95), "mean": float(x.mean()) if len(x) else None}


# --------------------------------------------------------------------------- loading
class Series:
    """Per-tree summed sample series for one role set. .p = primary only, .a = primary + burst."""

    def __init__(self, samples, roles):
        cols = [c for c in SUM_COLS if c in samples.columns]
        sub = samples[samples.role.isin(roles)]
        g = sub.groupby(["tree", "t", "primary"], sort=True)[cols].sum(min_count=1).reset_index()
        self.a = {tr: d.sort_values("t").reset_index(drop=True) for tr, d in g.groupby("tree")}
        self.p = {tr: d[d.primary == 1].reset_index(drop=True) for tr, d in self.a.items()}

    def window(self, tree, t0, t1, burst=False):
        d = (self.a if burst else self.p).get(tree)
        if d is None:
            return None
        t = d.t.values
        return d.iloc[np.searchsorted(t, t0, "left"):np.searchsorted(t, t1, "left")]


def clear_index(events):
    ev = events[events.clear_end.notna()] if len(events) else events
    return {(r.tree, k3(r.t_record)): (r.phase, r.clear_end) for r in ev.itertuples()}


def a_intervals(trees, samples):
    """A: intervals from each session file, restricted to the windows in which the tree was monitored."""
    segs = []
    for tid, d in trees.sort_values("t").groupby("tree"):
        rows = list(d.itertuples())
        last_t = samples.loc[samples.tree == tid, "t"].max() if (samples.tree == tid).any() else np.nan
        for i, r in enumerate(rows):
            if r.event not in ("attach", "switch") or not isinstance(r.path, str):
                continue
            t_next = rows[i + 1].t if i + 1 < len(rows) else np.inf
            seg_samples = samples.loc[(samples.tree == tid) & (samples.t >= r.t) & (samples.t < t_next), "t"]
            if seg_samples.empty:
                continue
            segs.append({"tree": tid, "kind": r.kind, "path": r.path, "t_from": r.t, "t_to": min(t_next, seg_samples.max(), last_t)})
    cache, out = {}, []
    for s in segs:
        if s["path"] not in cache:
            try:
                cache[s["path"]] = intervals(transitions_from_file(s["path"], s["kind"]))
            except OSError:
                cache[s["path"]] = []
        for iv in cache[s["path"]]:
            if iv["t0"] >= s["t_from"] and iv["t1"] <= s["t_to"]:
                out.append({"tree": s["tree"], **iv})
    return pd.DataFrame(out), pd.DataFrame(segs)


def b_intervals(runs_dir):
    out, runs = [], []
    for d in sorted(Path(runs_dir).glob("*__r*")):
        ev = d / "events.jsonl"
        res = d / "result.json"
        if not ev.exists() or not res.exists():
            continue
        tr = transitions_from_file(ev, "agent_b")
        evs = [json.loads(line) for line in open(ev) if line.strip()]
        t_done = next((e["t"] for e in evs if e["ev"] == "done"), None)
        r = json.loads(res.read_text())
        runs.append({"tree": d.name, "instance_id": r["instance_id"], "rep": int(d.name.rsplit("__r", 1)[1]),
                     "exit_status": r.get("exit_status"), "cost": r.get("cost"), "n_calls": r.get("n_calls"),
                     "model": r.get("model"), "t_start": evs[0]["t"] if evs else np.nan, "t_done": t_done})
        for iv in intervals(tr):
            if iv["phase"] != "done":
                out.append({"tree": d.name, **iv})
    return pd.DataFrame(out), pd.DataFrame(runs)


# --------------------------------------------------------------------------- per-interval metrics
def interval_metrics(ivs, measures, clears):
    """measures: {name: Series}. Adds cold fraction at the last primary sample after the governing clear."""
    rows = []
    for iv in ivs.itertuples():
        row = iv._asdict()
        row.pop("Index")
        c = clears.get((iv.tree, k3(iv.t_live)))
        row["clear_ok"] = bool(c and c[0] == iv.phase.replace("_unclosed", ""))
        for name, S in measures.items():
            w = S.window(iv.tree, iv.t0, iv.t1)
            row[f"{name}_nsamp"] = 0 if w is None else len(w)
            row[f"{name}_rss_mean_kb"] = w.rss.mean() if w is not None and len(w) else np.nan
            row[f"{name}_pss_mean_kb"] = w.pss.mean() if w is not None and len(w) else np.nan
            cold = rss_end = ref_end = t_s = np.nan
            if row["clear_ok"] and w is not None:
                after = w[w.t > c[1]]
                if len(after) and after.rss.iloc[-1] > 0:
                    rss_end, ref_end, t_s = after.rss.iloc[-1], after.ref.iloc[-1], after.t.iloc[-1]
                    cold = 1 - ref_end / rss_end
            row[f"{name}_cold"] = cold
            row[f"{name}_cold_kb"] = rss_end - ref_end
            row[f"{name}_rss_end_kb"] = rss_end
            row[f"{name}_coverage"] = (t_s - iv.t0) / iv.dur if iv.dur > 0 else np.nan
            if iv.phase == "tool_exec":  # bytes moved across the tool call, bracketed by the nearest samples
                d = S.a.get(iv.tree)
                if d is not None and len(d):
                    t = d.t.values
                    i0, i1 = np.searchsorted(t, iv.t0, "right") - 1, np.searchsorted(t, iv.t1, "left")
                    for col in CUM:
                        if col in d and i0 >= 0 and i1 < len(d):
                            v = d[col].iloc[i1] - d[col].iloc[i0]
                            row[f"{name}_d_{col}"] = v if v >= 0 else np.nan
        rows.append(row)
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- tasks (H3)
def a_tasks(ivs, segs):
    tasks = []
    for s in segs.itertuples():
        iv = ivs[(ivs.tree == s.tree) & (ivs.t0 >= s.t_from) & (ivs.t1 <= s.t_to)].sort_values("t0")
        cur = None
        for r in iv.itertuples():
            if r.phase.startswith("model_wait") and r.start_cause in ("prompt", "meta_prompt"):
                cur = {"tree": s.tree, "ts": r.t0, "tools": []}
            elif r.phase == "user_wait" and cur is not None:
                cur["te"] = r.t0
                tasks.append(cur)
                cur = None
            if cur is not None and r.phase == "tool_exec":
                cur["tools"].append((r.t0, r.t1))
    return tasks


def b_tasks(ivs, runs):
    out = []
    for r in runs.itertuples():
        if r.t_done is None or np.isnan(r.t_done):
            continue
        iv = ivs[(ivs.tree == r.tree) & (ivs.phase == "tool_exec")]
        out.append({"tree": r.tree, "ts": r.t_start, "te": r.t_done, "tools": list(zip(iv.t0, iv.t1))})
    return out


def task_peaks(tasks, S, ivs, col="rss", burst=False, need_tools=True):
    rows = []
    for tk in tasks:
        w = S.window(tk["tree"], tk["ts"], tk["te"], burst=burst)
        np_prim = 0 if w is None else int((w.primary == 1).sum())
        if w is None or np_prim < 5 or (need_tools and not tk["tools"]):
            continue
        i = int(np.argmax(w[col].values))
        tp = w.t.iloc[i]
        in_tool = any(a <= tp <= b for a, b in tk["tools"])
        ph = ivs[(ivs.tree == tk["tree"]) & (ivs.t0 <= tp) & (ivs.t1 > tp)].phase
        rows.append({"tree": tk["tree"], "ts": tk["ts"], "te": tk["te"], "n_tools": len(tk["tools"]), "t_peak": tp,
                     "peak_kb": w[col].iloc[i], "mean_kb": w[col].mean(), "in_tool": in_tool,
                     "peak_phase": ph.iloc[0] if len(ph) else "none"})
    return pd.DataFrame(rows)


# --------------------------------------------------------------------------- density (exploratory)
def density(S, ivm, name, trees_windows, include_user_wait=False):
    base, tier = [], []
    q_iv = ivm[(ivm.phase.isin(["model_wait"] + (["user_wait"] if include_user_wait else []))) &
               (ivm.dur >= 5) & ivm[f"{name}_cold"].notna()]
    for tree, t0, t1 in trees_windows:
        w = S.window(tree, t0, t1)
        if w is None or not len(w):
            continue
        rss = w.rss.values.astype(float)
        d = rss.copy()
        t = w.t.values
        for r in q_iv[q_iv.tree == tree].itertuples():
            m = (t >= r.t0) & (t < r.t1)
            d[m] = np.maximum(rss[m] - getattr(r, f"{name}_cold_kb"), 0)
        base.append(rss)
        tier.append(d)
    if not base:
        return None
    base, tier = np.concatenate(base) * KB, np.concatenate(tier) * KB
    cap = 0.9 * 128 * GIB
    out = {"sandbox_seconds": int(len(base))}
    for lab, f in (("mean", np.mean), ("p95", lambda x: np.percentile(x, 95))):
        mb, mt = float(f(base)), float(f(tier))
        out[lab] = {"per_sandbox_gib_base": mb / GIB, "per_sandbox_gib_tiered": mt / GIB,
                    "n_base": int(cap // mb), "n_tiered": int(cap // mt), "gain_pct": 100 * (mb / mt - 1)}
    return out


# --------------------------------------------------------------------------- figures
def style():
    plt.rcParams.update({"font.family": "DejaVu Sans", "font.size": 8, "axes.edgecolor": MUTED, "axes.labelcolor": INK2,
                         "xtick.color": MUTED, "ytick.color": MUTED, "axes.spines.top": False, "axes.spines.right": False,
                         "axes.grid": True, "grid.color": "#e8e7e3", "grid.linewidth": 0.6, "legend.frameon": False,
                         "figure.facecolor": "white", "axes.facecolor": SURF, "lines.linewidth": 1.6,
                         "axes.titlesize": 9, "axes.titlecolor": INK, "axes.titleweight": "bold", "axes.titlelocation": "left"})


def fig_timeline(panels, path):
    fig, axes = plt.subplots(len(panels), 1, figsize=(7, 2.3 * len(panels)), squeeze=False)
    for ax, (title, w, ivs, t0) in zip(axes[:, 0], panels):
        x = w.t.values - t0
        lo, hi = (x.min(), x.max()) if len(x) else (0, 1)
        span = hi - lo
        for r in ivs.itertuples():
            ph = r.phase.replace("_unclosed", "")
            if ph in PH_COLOR:
                a, b = max(r.t0 - t0, lo), min(r.t1 - t0, hi)
                if b <= a:
                    continue
                if b - a < 0.004 * span:  # widen sub-pixel tool calls so they stay visible
                    a, b = (a + b) / 2 - 0.002 * span, (a + b) / 2 + 0.002 * span
                ax.axvspan(a, b, color=PH_COLOR[ph], alpha=0.18, lw=0)
        ax.plot(x, w.rss.values / KB, color=INK, label="Rss")
        ax.plot(x, w.ref.values / KB, color=INK2, ls="--", lw=1.2, label="Referenced (since phase start)")
        ax.set_title(title)
        ax.set_ylabel("MiB")
        ax.set_ylim(bottom=0)
        ax.set_xlim(lo, hi)
    handles = [plt.Line2D([], [], color=INK, label="Rss"), plt.Line2D([], [], color=INK2, ls="--", label="Referenced since phase start")]
    handles += [matplotlib.patches.Patch(color=c, alpha=0.35, label=p) for p, c in PH_COLOR.items()]
    axes[-1, 0].set_xlabel("seconds from task start")
    fig.legend(handles=handles, loc="lower center", ncol=5, bbox_to_anchor=(0.5, -0.02))
    fig.tight_layout(rect=(0, 0.06, 1, 1))
    fig.savefig(path)
    plt.close(fig)


def fig_cold_cdf(groups_by_w, path):
    fig, axes = plt.subplots(1, len(groups_by_w), figsize=(7, 2.6), squeeze=False, sharey=True)
    for ax, (title, ivm, col) in zip(axes[0], groups_by_w):
        mw = ivm[(ivm.phase == "model_wait") & ivm[col].notna()]
        for lab, lo, hi, c in LEN_GROUPS:
            v = np.sort(mw[(mw.dur >= lo) & (mw.dur < hi)][col].values)
            if len(v):
                ax.step(v, np.arange(1, len(v) + 1) / len(v), where="post", color=c, label=f"{lab} (n={len(v)})")
        ax.axvline(0.5, color=MUTED, lw=0.8, ls=":")
        ax.set_title(title)
        ax.set_xlim(0, 1)
        ax.legend(loc="upper left", fontsize=7)
    axes[0, 0].set_ylabel("CDF over model_wait intervals")
    fig.supxlabel("cold fraction at interval end (1 − Referenced / Rss)", fontsize=8, color=INK2)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_peaks(panels, path):
    fig, axes = plt.subplots(1, len(panels), figsize=(7, 2.6), squeeze=False)
    for ax, (title, tp) in zip(axes[0], panels):
        if not len(tp):
            ax.set_title(title + " (no tasks)")
            continue
        tp = tp.assign(ratio=tp.peak_kb / tp.mean_kb).sort_values("ratio").reset_index(drop=True)
        for ph in [*PH_COLOR, "other"]:
            m = tp.peak_phase.map(lambda p: p if p in PH_COLOR else "other") == ph
            if m.any():
                ax.scatter(tp.index[m], tp.ratio[m], s=14, color=PH_COLOR.get(ph, OTHER), edgecolor=SURF, lw=0.6,
                           label=f"peak in {ph} ({int(m.sum())})", zorder=3)
        if tp.ratio.max() / max(tp.ratio.min(), 1e-9) > 10:
            ax.set_yscale("log")
        ax.set_title(title)
        ax.set_xlabel("task (sorted by ratio)")
        ax.set_ylabel("peak / mean Rss")
        ax.legend(loc="upper left", fontsize=7)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def fig_io(panels, path):
    fig, axes = plt.subplots(1, len(panels), figsize=(7, 2.6), squeeze=False, sharey=True)
    for ax, (title, series) in zip(axes[0], panels):
        for lab, v, color, ls in series:
            v = np.asarray(v, float)
            v = v[~np.isnan(v)]
            if not len(v):
                continue
            pos = np.sort(v[v > 0])
            zero = 1 - len(pos) / len(v)
            if len(pos):
                ax.step(pos, zero + np.arange(1, len(pos) + 1) / len(v), where="post", color=color, ls=ls,
                        label=f"{lab} (n={len(v)}, {100 * zero:.0f}% zero)")
        ax.set_xscale("log")
        ax.set_title(title)
        ax.set_xlabel("bytes per tool call")
        ax.legend(loc="lower right", fontsize=6.5)
    axes[0, 0].set_ylabel("CDF over tool calls")
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


# --------------------------------------------------------------------------- overhead
def overhead_stats(oh):
    if not len(oh):
        return None
    oh = oh.sort_values("t")
    # self_cpu_s / children_cpu_s are cumulative per daemon process; split on resets.
    runs = (oh.self_cpu_s.diff() < 0).cumsum()
    cpu = vm = dur = 0.0
    for _, d in oh.groupby(runs):
        dur += d.t.iloc[-1] - d.t.iloc[0]
        cpu += d.self_cpu_s.iloc[-1] - d.self_cpu_s.iloc[0]
        vm += d.children_cpu_s.iloc[-1] - d.children_cpu_s.iloc[0]
    return {"hours": dur / 3600, "sampler_cpu_pct": 100 * cpu / dur if dur else None,
            "vmtouch_cpu_pct": 100 * vm / dur if dur else None, "sample_ms": stats(oh.sample_ms),
            "sampler_rss_mb": stats(oh.self_rss_kb / KB), "procs_per_tick": stats(oh.n_procs)}


def main():
    RES.mkdir(exist_ok=True)
    FIG.mkdir(parents=True, exist_ok=True)
    style()
    N = {"meta": {"generated": time.strftime("%Y-%m-%d %H:%M:%S"),
                  "git": subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip(),
                  "prereg_sha256": hashlib.sha256((ROOT / "report" / "prereg.typ").read_bytes()).hexdigest()}}
    hyp = {"H1": {}, "H2": {}, "H3": {}}

    # ---- workload A
    A = {}
    sA, tA, eA = load(RAW / "A" / "samples-*.csv"), load(RAW / "A" / "trees-*.csv"), load(RAW / "A" / "events-*.csv")
    if len(sA):
        runs_meta = [json.load(open(f)) for f in sorted(glob.glob(str(RAW / "A" / "run-*.json")))]
        SA = Series(sA, ["root", "desc"])
        ivA, segA = a_intervals(tA, sA)
        ivmA = interval_metrics(ivA, {"tree": SA}, clear_index(eA))
        ivmA.to_csv(RES / "intervals_A.csv", index=False)
        tasksA = task_peaks(a_tasks(ivA, segA), SA, ivA)
        tasksA.to_csv(RES / "tasks_A.csv", index=False)
        lat = (eA.t_detect - eA.t_record).values
        A["monitor"] = {"t_start": min(r["t_start"] for r in runs_meta), "t_stop": max(r.get("t_stop", sA.t.max()) for r in runs_meta),
                        "trees": int(segA.tree.nunique()), "sessions": int(segA.path.nunique()),
                        "kinds": segA.drop_duplicates("tree").kind.value_counts().to_dict(),
                        "tree_hours": float((segA.t_to - segA.t_from).sum() / 3600),
                        "last_sample": time.strftime("%Y-%m-%d %H:%M", time.localtime(sA.t.max())),
                        "start": time.strftime("%Y-%m-%d %H:%M", time.localtime(min(r["t_start"] for r in runs_meta))),
                        "complete": any("t_stop" in r and r["t_stop"] >= r["until"] - 60 for r in runs_meta)}
        A["detect_latency_s"] = {**stats(lat), "max": float(np.max(lat)), "frac_lt_1s": float(np.mean(lat < 1.0))}
        A["phases"] = {p: stats(ivmA[ivmA.phase == p].dur) for p in ("model_wait", "tool_exec", "user_wait")}
        A["unclosed_model_wait"] = int((ivmA.phase == "model_wait_unclosed").sum())
        A["overhead"] = overhead_stats(load(RAW / "A" / "overhead-*.csv"))
        mc = load(RAW / "A" / "mincore-*.csv")
        if len(mc):
            mc = mc[mc.status == "ok"]
            A["mincore"] = {d: {"resident_frac": stats(g.resident_pages / g.total_pages), "size_gib": float(g.total_pages.median() * 4096 / GIB),
                                "files": int(g.files.median()), "scan_s": stats(g.elapsed)} for d, g in mc.groupby("dir")}
        windows = [(s.tree, s.t_from, s.t_to) for s in segA.itertuples()]
        A["density"] = {"model_wait": density(SA, ivmA, "tree", windows),
                        "model_and_user_wait": density(SA, ivmA, "tree", windows, include_user_wait=True)}
        mw = ivmA[(ivmA.phase == "model_wait") & (ivmA.dur >= 5)]
        hyp["H1"]["A"] = {**stats(mw.tree_cold), "excluded_no_sample": int(mw.tree_cold.isna().sum()),
                          "coverage": stats(mw.tree_coverage)}
        hyp["H2"]["A"] = stats(ivmA[ivmA.phase == "model_wait"].dur)
        hyp["H3"]["A"] = {"n": int(len(tasksA)), "frac_in_tool": float(tasksA.in_tool.mean()) if len(tasksA) else None}
        tbA = task_peaks(a_tasks(ivA, segA), SA, ivA, burst=True)
        tpA = task_peaks(a_tasks(ivA, segA), SA, ivA, col="pss")
        A["H3_sensitivity"] = {"with_burst": float(tbA.in_tool.mean()) if len(tbA) else None,
                               "pss": float(tpA.in_tool.mean()) if len(tpA) else None,
                               "peak_phase_counts": tasksA.peak_phase.value_counts().to_dict() if len(tasksA) else {}}
        A["table1"] = table1(ivmA, "tree")
        A["tool_io"] = {c: stats(ivmA.get(f"tree_d_{c}", pd.Series(dtype=float))) for c in ("rchar", "wchar", "read_bytes", "write_bytes")}
    N["A"] = A

    # ---- workload B
    B = {}
    sB, eB = load(RAW / "B" / "measure" / "samples.csv"), load(RAW / "B" / "measure" / "events.csv")
    if len(sB):
        ivB, runsB = b_intervals(RAW / "B" / "runs")
        runsB.to_csv(RES / "runs_B.csv", index=False)
        SB, SBH, SBA = Series(sB, ["sandbox"]), Series(sB, ["harness"]), Series(sB, ["sandbox", "harness"])
        ivmB = interval_metrics(ivB, {"sandbox": SB, "harness": SBH, "all": SBA}, clear_index(eB))
        ivmB.to_csv(RES / "intervals_B.csv", index=False)
        tk = b_tasks(ivB, runsB)
        tasksB = task_peaks(tk, SB, ivB, need_tools=False)
        tasksB.to_csv(RES / "tasks_B.csv", index=False)
        sel = pd.read_csv(RAW / "B" / "selection.csv")
        B["runs"] = {"n": int(len(runsB)), "instances": int(runsB.instance_id.nunique()),
                     "exit_status": runsB.exit_status.value_counts().to_dict(), "model": sorted(set(runsB.model.dropna())),
                     "cost_usd": float(runsB.cost.fillna(0).sum()), "model_calls": stats(runsB.n_calls),
                     "duration_s": stats(runsB.t_done - runsB.t_start), "repos": sel[sel.status == "ok"].repo.value_counts().to_dict(),
                     "pull_failed": int((sel.status != "ok").sum())}
        B["phases"] = {p: stats(ivmB[ivmB.phase == p].dur) for p in ("model_wait", "tool_exec")}
        B["overhead"] = overhead_stats(load(RAW / "B" / "measure" / "overhead.csv"))
        mc = load(RAW / "B" / "measure" / "mincore.csv")
        if len(mc):
            mc = mc[mc.status == "ok"]
            last = mc.sort_values("t").groupby("dir").tail(1)
            B["mincore_testbed"] = {"resident_frac": stats(last.resident_pages / last.total_pages),
                                    "size_mib": stats(last.total_pages * 4096 / (1 << 20)), "scan_s": stats(mc.elapsed)}
        windows = [(r.tree, r.t_start, r.t_done) for r in runsB.itertuples() if r.t_done]
        B["density_all"] = density(SBA, ivmB, "all", windows)
        B["density_sandbox"] = density(SB, ivmB, "sandbox", windows)
        mw = ivmB[(ivmB.phase == "model_wait") & (ivmB.dur >= 5)]
        hyp["H1"]["B"] = {**stats(mw.sandbox_cold), "excluded_no_sample": int(mw.sandbox_cold.isna().sum()),
                          "coverage": stats(mw.sandbox_coverage), "sandbox_rss_end_mib": stats(mw.sandbox_rss_end_kb / KB)}
        B["H1_secondary"] = {"harness": stats(mw.harness_cold), "harness_plus_sandbox": stats(mw.all_cold),
                             "harness_rss_end_mib": stats(mw.harness_rss_end_kb / KB)}
        hyp["H2"]["B"] = stats(ivmB[ivmB.phase == "model_wait"].dur)
        hyp["H3"]["B"] = {"n": int(len(tasksB)), "frac_in_tool": float(tasksB.in_tool.mean()) if len(tasksB) else None}
        sens = {"with_burst": task_peaks(tk, SB, ivB, burst=True, need_tools=False),
                "harness_plus_sandbox": task_peaks(tk, SBA, ivB, need_tools=False),
                "cgroup_memory_current": task_peaks(tk, SB, ivB, col="cg_current", need_tools=False),
                "pss": task_peaks(tk, SB, ivB, col="pss", need_tools=False)}
        B["H3_sensitivity"] = {k: (float(v.in_tool.mean()) if len(v) else None) for k, v in sens.items()}
        B["H3_sensitivity"]["peak_phase_counts"] = tasksB.peak_phase.value_counts().to_dict() if len(tasksB) else {}
        B["table1_sandbox"] = table1(ivmB, "sandbox")
        B["table1_all"] = table1(ivmB, "all")
        B["tool_io"] = {c: stats(ivmB.get(f"sandbox_d_{c}", pd.Series(dtype=float))) for c in ("cg_rbytes", "cg_wbytes")}
        rep = runsB.groupby("instance_id").filter(lambda g: len(g) > 1)
        if len(rep):
            pk = tasksB.set_index("tree")
            B["repeats"] = {iid: {"duration_s": [float(x) for x in (g.t_done - g.t_start)],
                                  "peak_mib": [float(pk.peak_kb.get(t, np.nan) / KB) for t in g.tree],
                                  "model_wait_median_s": [q(ivmB[(ivmB.tree == t) & (ivmB.phase == "model_wait")].dur, 50) for t in g.tree],
                                  "cold_median": [q(mw[mw.tree == t].sandbox_cold, 50) for t in g.tree]}
                            for iid, g in rep.groupby("instance_id")}
    N["B"] = B

    for h, thr, key in (("H1", 0.5, "median"), ("H2", 10.0, "median"), ("H3", 0.8, "frac_in_tool")):
        for w, v in hyp[h].items():
            v["threshold"] = thr
            v["holds"] = None if v.get(key) is None else bool(v[key] >= thr)
    N["hypotheses"] = hyp

    # ---- overhead bench
    ob, ol = load(RAW / "bench" / "overhead.csv"), load(RAW / "bench" / "latency.csv")
    if len(ob):
        base = ob[ob.cond == "none"].pages_per_s.mean()
        N["bench"] = {c: {"pages_per_s_mean": float(g.pages_per_s.mean()), "pages_per_s_sd": float(g.pages_per_s.std()),
                          "rel_to_none_pct": float(100 * (g.pages_per_s.mean() / base - 1)), "minflt_mean": float(g.minflt.mean()),
                          "reps": int(len(g))} for c, g in ob.groupby("cond")}
        N["bench"]["latency_ms"] = {op: stats(g.ms) for op, g in ol.groupby("op")} if len(ol) else {}

    # ---- figures
    panels = []
    if len(sA) and len(tasksA):
        cand = tasksA[(tasksA.te - tasksA.ts).between(120, 600)]
        cand = cand if len(cand) else tasksA
        tk1 = cand.sort_values(["n_tools", "ts"], ascending=[False, True]).iloc[0]
        t0, t1 = tk1.ts - 20, tk1.te + 60
        panels.append((f"A: one user turn ({tk1.n_tools} tool calls), whole tree", SA.window(tk1.tree, t0, t1),
                       ivA[(ivA.tree == tk1.tree) & (ivA.t1 > t0) & (ivA.t0 < t1)], tk1.ts))
        N["A"]["fig1_task"] = {"tree": tk1.tree, "ts": float(tk1.ts), "te": float(tk1.te), "n_tools": int(tk1.n_tools)}
    if len(sB) and len(tasksB):
        r0 = runsB[runsB.rep == 0].assign(d=lambda d: d.t_done - d.t_start).dropna(subset=["d"]).sort_values("d")
        if len(r0):
            rr = r0.iloc[len(r0) // 2]
            panels.append((f"B: {rr.tree} (median-length run), harness + sandbox", SBA.window(rr.tree, rr.t_start, rr.t_done),
                           ivB[ivB.tree == rr.tree], rr.t_start))
            N["B"]["fig1_task"] = {"tree": rr.tree, "duration_s": float(rr.d)}
    if panels:
        fig_timeline(panels, FIG / "fig1_timeline.pdf")
    cdf = ([("A: whole tree", ivmA, "tree_cold")] if len(sA) else []) + ([("B: sandbox", ivmB, "sandbox_cold"), ("B: harness + sandbox", ivmB, "all_cold")] if len(sB) else [])
    if cdf:
        fig_cold_cdf(cdf, FIG / "fig2_cold_cdf.pdf")
    pk = ([("A: user turns", tasksA)] if len(sA) else []) + ([("B: runs (sandbox Rss)", tasksB)] if len(sB) else [])
    if pk:
        fig_peaks(pk, FIG / "fig3_peaks.pdf")
    io = []
    if len(sA):
        g = ivmA[ivmA.phase == "tool_exec"]
        io.append(("A: whole tree, per tool call", [("read (syscall)", g.get("tree_d_rchar"), "#2a78d6", "-"),
                                                    ("write (syscall)", g.get("tree_d_wchar"), "#eb6834", "-"),
                                                    ("read (storage)", g.get("tree_d_read_bytes"), "#2a78d6", "--"),
                                                    ("write (storage)", g.get("tree_d_write_bytes"), "#eb6834", "--")]))
    if len(sB):
        g = ivmB[ivmB.phase == "tool_exec"]
        io.append(("B: sandbox cgroup, per tool call", [("read (storage)", g.get("sandbox_d_cg_rbytes"), "#2a78d6", "--"),
                                                        ("write (storage)", g.get("sandbox_d_cg_wbytes"), "#eb6834", "--")]))
    if io:
        fig_io(io, FIG / "fig4_tool_io.pdf")

    (ROOT / "numbers.json").write_text(json.dumps(N, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    print(json.dumps(N["hypotheses"], indent=1, default=str))


def table1(ivm, name):
    out = {}
    for p in ("model_wait", "tool_exec", "user_wait"):
        g = ivm[ivm.phase == p]
        if not len(g):
            continue
        out[p] = {"dur_s": stats(g.dur), "rss_mib": stats(g[f"{name}_rss_mean_kb"] / KB),
                  "pss_mib": stats(g[f"{name}_pss_mean_kb"] / KB), "cold": stats(g[f"{name}_cold"])}
    return out


if __name__ == "__main__":
    main()
