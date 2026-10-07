# Run log

## Workload A (memtrace-a.service, ws4)

- Start: 2026-10-06 01:18:55 Asia/Taipei (daemon t_start 1791220735.300, raw/A/run-1791220735.json).
- Pre-registered end: 2026-10-08 09:00:00 Asia/Taipei (--until 2026-10-08T09:00).
- Stopped early: SIGTERM sent 2026-10-08 00:37:16.428 Asia/Taipei to PID 2366097 (python -m memtrace.daemon_a); daemon recorded t_stop 1791391036.452 = 2026-10-08 00:37:16 Asia/Taipei and exited with status 0.
- Reason: the author cannot operate on 2026-10-08. No hypothesis, threshold, or analysis semantics changed.
- Stop method: README/STATUS document no stop command and the repo has no Makefile or scripts/, so per instruction the running sampler process was sent SIGTERM directly (its handler sets Sampler.stop, closes all CSVs, then writes t_stop).
- Claude Code processes alive on ws4 at the stop: 1060331, 2343579 (the session that performed this stop and the finalization), 4004800.

## Workload B

- Completed 2026-10-06 02:55 (30 runs); untouched since.
