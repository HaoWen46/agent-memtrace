"""Workload B: pre-registered SWE-bench Lite sample, run with mini-SWE-agent under one sampler.

Selection: sort Lite test ids, shuffle with random.Random(SEED), walk in order and keep the first N
whose image pulls; the first REPEAT_IDS of those get REPEATS extra runs. Image pulls happen before and
outside the measured window. Runs execute CONCURRENCY at a time, each a separate tree:
agent_b harness process tree (role 'harness') + its container cgroup (role 'sandbox').
"""
import argparse
import csv
import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

from . import procfs
from .sampler import Sampler, Source, Tree

SEED, N, REPEAT_IDS, REPEATS = 20261006, 24, 3, 2
ROOT = Path(__file__).resolve().parent.parent
UID = os.getuid()
DOCKER = os.path.expanduser("~/bin/docker")


def docker_env():
    env = dict(os.environ, DOCKER_HOST=f"unix:///run/user/{UID}/docker.sock",
               PATH=os.path.expanduser("~/bin") + ":" + os.environ.get("PATH", ""))
    keyfile = Path("~/.config/agent-memtrace/env").expanduser()
    if keyfile.exists():
        for line in keyfile.read_text().splitlines():
            k, sep, v = line.strip().partition("=")
            if sep and not k.startswith("#"):
                env[k.removeprefix("export ").strip()] = v.strip().strip("'\"")
    env.update(MSWEA_COST_TRACKING="ignore_errors", MSWEA_DOCKER_EXECUTABLE=DOCKER)
    return env


def order():
    from datasets import load_dataset
    from minisweagent.run.benchmarks.swebench import get_swebench_docker_image_name
    ds = {r["instance_id"]: dict(r) for r in load_dataset("princeton-nlp/SWE-bench_Lite", split="test")}
    ids = sorted(ds)
    random.Random(SEED).shuffle(ids)
    return [(iid, ds[iid], get_swebench_docker_image_name(ds[iid])) for iid in ids]


def select(out, env, n=N):
    """Pull images in the pre-registered order until N succeed; returns selected instances."""
    sel_path = out / "selection.csv"
    done = {}
    if sel_path.exists():
        done = {r["instance_id"]: r for r in csv.DictReader(open(sel_path))}
    chosen = []
    with open(sel_path, "a", newline="") as f:
        w = csv.DictWriter(f, ["rank", "instance_id", "repo", "image", "status", "pull_s"])
        if not done:
            w.writeheader()
        for rank, (iid, inst, image) in enumerate(order()):
            if len(chosen) == n:
                break
            if iid in done:
                if done[iid]["status"] == "ok":
                    chosen.append(inst)
                continue
            t0 = time.time()
            r = subprocess.run([DOCKER, "pull", "-q", image], env=env, capture_output=True, text=True, timeout=1800)
            status = "ok" if r.returncode == 0 else "pull_failed:" + r.stderr.strip()[-120:].replace(",", ";")
            w.writerow({"rank": rank, "instance_id": iid, "repo": inst["repo"], "image": image, "status": status,
                        "pull_s": f"{time.time() - t0:.1f}"})
            f.flush()
            if status == "ok":
                chosen.append(inst)
            print(rank, iid, status, flush=True)
    return chosen


class Runs:
    def __init__(self, out, queue, env, args):
        self.out, self.queue, self.env, self.a = out, queue, env, args
        self.running = {}

    def hook(self, s):
        for tid, (p, tree) in list(self.running.items()):
            if p.poll() is not None:
                s.remove(tid)
                del self.running[tid]
        while self.queue and len(self.running) < self.a.concurrency:
            inst, rep = self.queue.pop(0)
            run_id = f"{inst['instance_id']}__r{rep}"
            d = self.out / "runs" / run_id
            if (d / "result.json").exists():
                continue
            d.mkdir(parents=True, exist_ok=True)
            (d / "instance.json").write_text(json.dumps(inst))
            ev = d / "events.jsonl"
            ev.write_text("")
            log = open(d / "agent.log", "w")
            p = subprocess.Popen([sys.executable, "-m", "memtrace.agent_b", "--instance", str(d / "instance.json"),
                                  "--out", str(d), "--model", self.a.model, "--step-limit", str(self.a.step_limit),
                                  "--cost-limit", str(self.a.cost_limit), "--wall-limit", str(self.a.wall_limit)],
                                 cwd=ROOT, env=self.env, stdout=log, stderr=subprocess.STDOUT)
            tree = Tree(run_id, "agent_b", p.pid, procfs.stat(p.pid)["starttime"], meta={"session": run_id})

            def on_record(rec, tree=tree):
                if rec.get("ev") == "container":
                    tree.sandbox_cg = procfs.cgroup_of(rec["pid"])
                    tree.workdirs = [f"/proc/{rec['pid']}/root/testbed"]
                    s.log_tree("container", tree)

            tree.source = Source(str(ev), "agent_b", on_record=on_record)
            s.add(tree)
            self.running[run_id] = (p, tree)
        if not self.queue and not self.running:
            s.stop = True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(ROOT / "raw" / "B"))
    ap.add_argument("--pull-only", action="store_true")
    ap.add_argument("--model", default=None)
    ap.add_argument("--concurrency", type=int, default=3)
    ap.add_argument("--step-limit", type=int, default=100)
    ap.add_argument("--cost-limit", type=float, default=1.0)
    ap.add_argument("--wall-limit", type=int, default=2400)
    ap.add_argument("--limit-runs", type=int, default=None, help="pilot: only the first K runs")
    ap.add_argument("--n", type=int, default=N, help="instances to select (pre-registered: 24; smaller only for smoke tests)")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    env = docker_env()
    chosen = select(out, env, a.n)
    if a.pull_only:
        return
    a.model = a.model or env.get("MEMTRACE_MODEL")
    queue = [(inst, 0) for inst in chosen] + [(inst, r) for inst in chosen[:REPEAT_IDS] for r in range(1, REPEATS + 1)]
    queue = queue[: a.limit_runs] if a.limit_runs else queue
    (out / f"config-{int(time.time())}.json").write_text(json.dumps({**vars(a), "seed": SEED, "runs": len(queue)}))
    s = Sampler(str(out / "measure"), rotate=False, clear=True)
    runs = Runs(out, queue, env, a)
    s.run(hook=runs.hook, hook_every=1.0)


if __name__ == "__main__":
    main()
