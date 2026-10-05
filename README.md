# agent-memtrace

Phase-aware memory characterization of coding-agent sandboxes (no root): 1 Hz `/proc` sampling of agent process trees, `clear_refs`-based cold-memory measurement per phase (model_wait / tool_exec / user_wait), phases cut from Claude Code / Codex session JSONL or an instrumented mini-SWE-agent loop.

- `memtrace/sampler.py` sampler; `memtrace/phases.py` phase segmentation (shared by live triggering and analysis).
- Workload A (live sessions on this host): `uv run python -m memtrace.daemon_a --out raw/A --until 2026-10-08T09:00`, normally launched with `systemd-run --user` so it is not inside a monitored tree.
- Workload B (SWE-bench Lite, rootless Docker): `uv run python -m memtrace.run_b` (`--pull-only` to pre-pull); key and model in `~/.config/agent-memtrace/env` (`DEEPSEEK_API_KEY=`, `MEMTRACE_MODEL=`).
- Overhead: `uv run python -m bench.overhead raw/bench`. Analysis: `uv run python -m memtrace.analyze`; report: `typst compile --root . report/report.typ`.
- Pre-registration is `report/prereg.typ`, frozen at its first commit.
