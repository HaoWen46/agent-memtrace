"""One mini-SWE-agent run on one SWE-bench Lite instance, emitting phase events for the sampler.

Mirrors minisweagent.run.benchmarks.swebench.process_instance; the only change is an agent subclass
that writes {"t": time.time(), "ev": ...} lines around every model query and every tool execution.
"""
import argparse
import json
import os
import subprocess
import time
import traceback
from pathlib import Path

from minisweagent.agents.default import DefaultAgent
from minisweagent.config import get_config_from_spec
from minisweagent.models import get_model
from minisweagent.run.benchmarks.swebench import get_sb_environment
from minisweagent.utils.serialize import recursive_merge


class InstrumentedAgent(DefaultAgent):
    def __init__(self, *args, events, **kwargs):
        super().__init__(*args, **kwargs)
        self._events = events
        self._step = 0

    def emit(self, ev, **kw):
        self._events.write(json.dumps({"t": time.time(), "ev": ev, **kw}) + "\n")
        self._events.flush()

    def query(self):
        self.emit("model_start", step=self._step)
        try:
            msg = super().query()
        except BaseException as e:
            self.emit("model_end", step=self._step, error=type(e).__name__)
            raise
        self.emit("model_end", step=self._step, n_actions=len(msg.get("extra", {}).get("actions", [])),
                  msg_t=msg.get("extra", {}).get("timestamp"))
        return msg

    def execute_actions(self, message):
        outputs = []
        try:
            for action in message.get("extra", {}).get("actions", []):
                self.emit("tool_start", step=self._step)
                try:
                    outputs.append(self.env.execute(action))
                finally:
                    self.emit("tool_end", step=self._step)
        finally:
            self._step += 1
        return self.add_messages(*self.model.format_observation_messages(message, outputs, self.get_template_vars()))


def _out(*actions):
    return {"role": "assistant", "content": "smoke", "extra": {"actions": [{"command": c} for c in actions]}}


# Pipeline smoke test without an API key: scripted model delays (/sleep) and real container commands.
SMOKE = [_out("/sleep 6"), _out("cd /testbed && git status | head -5"), _out("/sleep 7"),
         _out("python -c 'import time; x = bytearray(300 << 20); time.sleep(3)'"), _out("/sleep 5"),
         _out("grep -rl import /testbed | head -3"), _out("echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT")]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance", required=True, help="path to the instance JSON")
    ap.add_argument("--out", required=True, help="run directory")
    ap.add_argument("--model", required=True)
    ap.add_argument("--step-limit", type=int, required=True)
    ap.add_argument("--cost-limit", type=float, required=True)
    ap.add_argument("--wall-limit", type=int, required=True)
    a = ap.parse_args()
    instance = json.loads(Path(a.instance).read_text())
    out = Path(a.out)
    config = get_config_from_spec("swebench.yaml")
    config = recursive_merge(config, {
        "model": {"model_name": a.model, "cost_tracking": "ignore_errors"},
        "agent": {"step_limit": a.step_limit, "cost_limit": a.cost_limit, "wall_time_limit_seconds": a.wall_limit},
    })
    if a.model == "smoke":
        config["model"] = {"model_class": "deterministic", "model_name": "deterministic", "outputs": SMOKE}
    events = open(out / "events.jsonl", "a", buffering=1)
    agent, env, model, exit_status, result, extra = None, None, None, None, None, {}
    try:
        model = get_model(config=config.get("model", {}))
        env = get_sb_environment(config, instance)
        pid = int(subprocess.run([env.config.executable, "inspect", "-f", "{{.State.Pid}}", env.container_id],
                                 capture_output=True, text=True, check=True).stdout)
        events.write(json.dumps({"t": time.time(), "ev": "container", "cid": env.container_id, "pid": pid}) + "\n")
        agent = InstrumentedAgent(model, env, events=events, **config.get("agent", {}))
        info = agent.run(instance["problem_statement"])
        exit_status, result = info.get("exit_status"), info.get("submission")
    except Exception as e:
        exit_status, result = type(e).__name__, ""
        extra = {"traceback": traceback.format_exc(), "exception_str": str(e)}
    finally:
        events.write(json.dumps({"t": time.time(), "ev": "done", "exit_status": exit_status}) + "\n")
        events.flush()
        if agent is not None:
            agent.save(out / "traj.json", {"info": {"exit_status": exit_status, "submission": result, **extra},
                                           "instance_id": instance["instance_id"]})
        (out / "result.json").write_text(json.dumps({"instance_id": instance["instance_id"], "exit_status": exit_status,
                                                     "model": a.model, "cost": getattr(agent, "cost", None),
                                                     "n_calls": getattr(agent, "n_calls", None), **extra}))
        if env is not None and getattr(env, "container_id", None):
            subprocess.run([env.config.executable, "rm", "-f", env.container_id], capture_output=True, timeout=120)
            env.container_id = None
        os._exit(0)


if __name__ == "__main__":
    main()
