// Pre-registration. Frozen 2026-10-06 before workload A/B data collection; do not edit after the
// freeze commit. Any deviation goes in the report's deviations section, never here.

== 預先登記（2026-10-06 凍結，於 A、B 資料收集前提交）

=== 假設（原文，門檻不改；不成立照實報告）
- *H1*：在長度 ≥ 5 秒的等模型區間內，行程樹 RSS 中未被觸碰（Referenced=0）的比例中位數 ≥ 50%。
- *H2*：等模型區間長度中位數 ≥ 10 秒。
- *H3*：每個任務的記憶體尖峰落在工具執行區間內的比例 ≥ 80%（重現 AgentCgroup 的「尖峰由工具呼叫驅動」）。

=== 行程樹
- *A*：本使用者在 ws4 上每個 Claude Code（comm `claude`）或 Codex（comm `codex*`）行程為根，加上以 `/proc/<pid>/task/*/children` 遞迴收集的全部子孫，每次取樣重新收集。位於另一個已追蹤根之下的 agent 行程併入外層樹。量測器自身及其子行程排除。
- *B*：主要對象為 sandbox，即 SWE-bench 容器 cgroup 的 `cgroup.procs` 內全部行程（`docker exec` 產生的行程不是容器 init 的子孫，故以 cgroup 定義成員，與 AgentCgroup 相同）。harness（mini-SWE-agent 行程及其子孫，含 docker CLI）另列為次要分析，不進入 H1–H3 主判定。

=== 取樣與量
- 1 Hz，落在整數秒（`time.time()`），稱 primary 樣本。即時偵測到 tool_exec 期間另以 10 Hz 補樣（primary=0）。H1–H3 主判定只用 primary 樣本；含補樣的結果僅作敏感度分析。
- 每行程讀 `smaps_rollup`（Rss、Pss、Referenced、Swap 等）、`io`、`stat`。樹的 Rss、Referenced 為成員加總（kB）。冷比例 = Σ(Rss − Referenced) / ΣRss。

=== 階段
- *A*：由 session JSONL 記錄時間戳以 `memtrace/phases.py` 狀態機切割（即時觸發與事後切割用同一份程式碼）。tool_exec = [回應中的 tool_use 記錄 → 該批最後一個 tool_result]；model_wait = [user 提示或最後一個 tool_result → 下一筆含 tool_use 或結束該輪的 assistant 記錄]；user_wait = [該輪最後一個 assistant 區塊 → 下一個 user 提示]。subagent（sidechain）屬於父 session 的 tool_exec。未被模型回應（或使用者中斷）關閉的 model_wait 標為 unclosed 並排除。
- *B*：由插樁的 agent 迴圈事件切割（同一時鐘）：model_wait = [model_start, model_end]，tool_exec = [tool_start, tool_end]。B 沒有 user_wait。

=== clear_refs
- 每次即時偵測到階段轉換（model_wait、tool_exec、user_wait 開始），對樹內所有成員寫入 `1` 到 `/proc/<pid>/clear_refs`。一個區間的「主控 clear」為該區間起點轉換所觸發的 clear。

=== 統計量與判定
- *H1*：每個長度 ≥ 5 秒、有主控 clear 的 model_wait 區間，取 clear 完成之後、區間結束之前的最後一個 primary 樣本計算冷比例；沒有這種樣本的區間排除並計數。A、B 分別判定：中位數 ≥ 0.50 為成立。
- *H2*：所有已關閉、且完整落在監測窗內（A：區間起點晚於該樹被掛上的時間）的 model_wait 區間長度中位數 ≥ 10 秒為成立。A、B 分別判定。
- *H3*：任務定義 —— A：一個使用者回合（由提示觸發的 model_wait 起到下一個 user_wait 開始），且含 ≥ 1 個 tool_exec 區間與 ≥ 5 個 primary 樣本；B：一次執行（原始與重跑各算一次）。尖峰 = 任務內樹 Rss 最大的 primary 樣本（同值取最早）；樣本時間落在 tool_exec 區間內即算「在工具內」。比例 ≥ 0.80 為成立。A、B 分別判定。
- 次要（不影響判定）：B 的 harness+sandbox 合計、B 的 cgroup `memory.current`、含 10 Hz 補樣的版本、Pss 版本。

=== 工作負載與排除
- *A*：A daemon 啟動起至 2026-10-08 09:00（本地時間），只限 ws4；含建置本工具的這個 session。daemon 啟動前的試跑資料排除。
- *B*：SWE-bench Lite test split，id 排序後以 `random.Random(20261006)` 洗牌，依序取映像檔拉取成功的前 24 題；其中前 3 題各重跑 2 次（共 30 次執行）。同時 3 個執行。mini-SWE-agent 2.4.6、內建 `swebench.yaml` 設定，step limit 100、cost limit 1 USD、wall-time limit 2400 秒，模型為 DeepSeek flash 等級（確切 ID 記於 raw）。rootless Docker 29.8.2（非 venv）。試跑（`--limit-runs`）寫入獨立目錄並排除。
- 128 GB 主機容量換算為探索性分析，不屬預先登記。
