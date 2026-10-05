"""Phase segmentation: session records -> transitions (t, phase, cause) -> intervals.

The same phaser classes drive live clear_refs triggering (sampler) and post-hoc interval building
(analysis), so boundaries are identical in both. Phases: model_wait, tool_exec, user_wait, plus
B-only 'agent' (harness bookkeeping between model and tool) and 'done'.
"""
import json
from datetime import datetime

# A model_wait interval counts only if it is closed by the model answering (or an explicit interrupt).
MODEL_CLOSERS = {"assistant_tool", "assistant_end", "interrupt", "turn_end", "model_end"}


def iso(ts):
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()


class _Phaser:
    def __init__(self):
        self.phase = None
        self.pending = {}

    def _go(self, t, phase, cause, **meta):
        if phase == self.phase:
            return []
        self.phase = phase
        return [{"t": t, "phase": phase, "cause": cause, **meta}]


class ClaudePhaser(_Phaser):
    """Claude Code session JSONL (main thread only; sidechain/subagent work counts as the parent's tool_exec)."""

    def feed(self, d):
        typ = d.get("type")
        if d.get("isSidechain") or typ not in ("user", "assistant", "system") or not d.get("timestamp"):
            return []
        t = iso(d["timestamp"])
        if typ == "system":
            if d.get("subtype") == "turn_duration" and not self.pending:
                return self._go(t, "user_wait", "turn_end")
            return []
        m = d.get("message") or {}
        c = m.get("content")
        blocks = [b for b in c if isinstance(b, dict)] if isinstance(c, list) else []
        if typ == "assistant":
            uses = [b for b in blocks if b.get("type") == "tool_use"]
            req = d.get("requestId")
            if uses:
                for b in uses:
                    self.pending[b.get("id")] = b.get("name", "")
                return self._go(t, "tool_exec", "assistant_tool", tools="|".join(b.get("name", "") for b in uses), req=req)
            if m.get("stop_reason") in ("end_turn", "stop_sequence", "max_tokens", "refusal") and not self.pending:
                return self._go(t, "user_wait", "assistant_end", req=req)
            if self.phase in (None, "user_wait"):
                return self._go(t, "model_wait", "assistant_unprompted")
            return []
        results = [b for b in blocks if b.get("type") == "tool_result"]
        if results:
            for b in results:
                self.pending.pop(b.get("tool_use_id"), None)
            if not self.pending and self.phase == "tool_exec":
                return self._go(t, "model_wait", "tool_result")
            return []
        if d.get("sourceToolUseID"):
            return []
        text = c if isinstance(c, str) else " ".join(str(b.get("text", "")) for b in blocks)
        text = text.lstrip()
        if text.startswith("[Request interrupted"):
            self.pending.clear()
            return self._go(t, "user_wait", "interrupt")
        if text.startswith("<local-command-stdout>"):
            return self._go(t, "user_wait", "local_command")
        if d.get("isMeta"):
            if self.phase in (None, "user_wait"):
                return self._go(t, "model_wait", "meta_prompt")
            return []
        self.pending.clear()
        return self._go(t, "model_wait", "prompt")


class CodexPhaser(_Phaser):
    """Codex rollout JSONL."""

    def feed(self, d):
        if not d.get("timestamp"):
            return []
        t = iso(d["timestamp"])
        p = d.get("payload") or {}
        pt = p.get("type")
        if d.get("type") == "event_msg":
            if pt == "task_started":
                self.pending.clear()
                return self._go(t, "model_wait", "prompt")
            if pt in ("task_complete", "turn_aborted"):
                self.pending.clear()
                return self._go(t, "user_wait", "assistant_end" if pt == "task_complete" else "interrupt")
            return []
        if d.get("type") != "response_item" or not pt:
            return []
        cid = p.get("call_id") or p.get("id")
        if pt in ("function_call", "custom_tool_call", "local_shell_call"):
            self.pending[cid] = p.get("name", pt)
            return self._go(t, "tool_exec", "assistant_tool", tools=p.get("name", pt))
        if pt.endswith("_call_output"):
            self.pending.pop(cid, None)
            if not self.pending and self.phase == "tool_exec":
                return self._go(t, "model_wait", "tool_result")
        return []


class AgentBPhaser(_Phaser):
    """Events written by memtrace.agent_b (already epoch-timestamped by the harness itself)."""

    MAP = {"model_start": ("model_wait", "prompt"), "model_end": ("agent", "model_end"),
           "tool_start": ("tool_exec", "assistant_tool"), "tool_end": ("agent", "tool_end"),
           "done": ("done", "done")}

    def feed(self, d):
        if d.get("ev") not in self.MAP:
            return []
        phase, cause = self.MAP[d["ev"]]
        if d["ev"] == "model_start" and d.get("step", 0) > 0:
            cause = "tool_result"
        if d["ev"] == "model_end" and d.get("error"):
            cause = "model_error"
        self.phase = None  # every B event is a real boundary, even model_end -> agent -> model_start
        return self._go(d["t"], phase, cause, step=d.get("step", ""))


PHASERS = {"claude": ClaudePhaser, "codex": CodexPhaser, "agent_b": AgentBPhaser}


def transitions_from_file(path, kind):
    """Post-hoc: all transitions in a session file. Claude user_wait starts are moved to the last
    assistant block of the same request (records of one response carry block-completion times)."""
    ph = PHASERS[kind]()
    out, req_last = [], {}
    with open(path, errors="replace") as f:
        for line in f:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            if kind == "claude" and d.get("type") == "assistant" and d.get("timestamp") and not d.get("isSidechain"):
                r = d.get("requestId")
                req_last[r] = max(req_last.get(r, 0.0), iso(d["timestamp"]))
            out.extend(ph.feed(d))
    for i, tr in enumerate(out):
        if tr["phase"] == "user_wait" and tr.get("req") in req_last:
            nxt = out[i + 1]["t"] if i + 1 < len(out) else float("inf")
            tr["t_live"] = tr["t"]
            tr["t"] = min(max(tr["t"], req_last[tr["req"]]), nxt)
    return out


def intervals(trans, t_stop=None):
    """Consecutive transitions -> intervals. The last one is closed at t_stop if given."""
    rows = []
    ends = trans[1:] + ([{"t": t_stop, "cause": "stop", "phase": None}] if t_stop else [])
    for a, b in zip(trans, ends):
        phase = a["phase"]
        if phase == "model_wait" and b["cause"] not in MODEL_CLOSERS:
            phase = "model_wait_unclosed"
        rows.append({"phase": phase, "t0": a["t"], "t1": b["t"], "dur": b["t"] - a["t"],
                     "start_cause": a["cause"], "end_cause": b["cause"], "tools": a.get("tools", ""),
                     "t_live": a.get("t_live", a["t"])})
    return rows
