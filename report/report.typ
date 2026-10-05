#set document(title: "agent-memtrace")
#set page(paper: "a4", margin: (x: 1.8cm, y: 1.7cm), numbering: "1")
#set text(font: ("Libertinus Serif", "AR PL UMing TW"), size: 9.5pt, lang: "zh", region: "tw")
#set par(justify: true, leading: 0.62em)
#set heading(numbering: "1.")
#show heading: set block(above: 1em, below: 0.6em)
#show raw: set text(font: ("DejaVu Sans Mono", "AR PL UMing TW"), size: 8pt)
#show figure.caption: set text(size: 8.5pt)
#set table(stroke: (x, y) => if y == 0 { (bottom: 0.6pt) } else { none }, inset: (x: 4pt, y: 2.5pt))

// Every number below is read from numbers.json, which memtrace/analyze.py recomputes from raw/.
#let N = json("../numbers.json")
#let A = N.at("A", default: (:))
#let B = N.at("B", default: (:))
#let H = N.hypotheses
#let get(d, ..path) = {
  let x = d
  for k in path.pos() { x = if type(x) == dictionary { x.at(k, default: none) } else { none } }
  x
}
#let f(x, d: 1) = if x == none { "—" } else { str(calc.round(float(x), digits: d)) }
#let pct(x, d: 0) = if x == none { "—" } else { str(calc.round(100 * float(x), digits: d)) + "%" }
#let verdict(v) = if v == none { "無資料" } else if v { "成立" } else { "不成立" }

#align(center)[
  #text(14pt, weight: "bold")[agent-memtrace：階段感知的 coding agent sandbox 記憶體特徵化] \
  #v(2pt)
  #text(8.5pt, fill: rgb("#52514e"))[資料產生 #N.meta.generated · git #N.meta.git · 預先登記 sha256 #raw(N.meta.prereg_sha256.slice(0, 16))…]
]

#include "summary.typ"

= 方法

== 量測器
量測器（`memtrace/sampler.py`，僅用標準函式庫）以一般使用者身分執行，不需 root、不改 kernel、不裝系統套件。每個行程讀 `/proc/<pid>/smaps_rollup`（Rss、Pss、Referenced、Swap）、`/proc/<pid>/io`（rchar、wchar、read_bytes、write_bytes）、`/proc/<pid>/stat`（utime+stime+cutime+cstime、page fault）。已回收子行程的 I/O 與 CPU 會併入父行程的計數器（實測確認），故對樹內存活成員加總即得累計值。B 的 `docker exec` 行程由樹外的 containerd-shim 回收，因此 sandbox 的 I/O 與 CPU 改用容器 cgroup 的 `io.stat`、`cpu.stat`，並另記 `memory.current`、`memory.stat`。

冷記憶體：每次即時偵測到階段轉換，就對樹內所有成員寫 `1` 到 `/proc/<pid>/clear_refs`，清除 accessed 位元；之後任一樣本的 Referenced 即為「自本階段開始以來被觸碰過的頁」，冷 = Rss − Referenced。區間結束前最後一個 1 Hz 樣本給出該區間的冷比例。檔案快取側每 10 秒以 `vmtouch`（mmap + mincore，不觸碰頁面）估工作目錄的常駐頁數；單次掃描超過 0.5 秒的目錄改為每 20 倍掃描時間掃一次，以限制 CPU 開銷。

== 階段切割與時間對齊
`memtrace/phases.py` 的狀態機同時用於即時觸發 clear_refs 與事後切割區間，兩者邊界一致。A 讀 Claude Code session JSONL（另支援 Codex rollout），只取時間戳與記錄型別，不存任何訊息內容。B 由插樁的 mini-SWE-agent 迴圈在每次模型呼叫與工具執行前後寫事件。量測器樣本、JSONL 時間戳與 B 事件都是同一主機的 `time.time()`，故區間邊界與樣本的對齊誤差即時間戳本身的誤差；即時偵測延遲只影響 clear 的時機（A：中位數 #f(get(A, "detect_latency_s", "median"), d: 2) 秒，p95 #f(get(A, "detect_latency_s", "p95"), d: 2) 秒，#pct(get(A, "detect_latency_s", "frac_lt_1s")) 小於 1 秒），並以每個區間的「覆蓋率」（clear 後首個可用樣本到區間結束占區間長度的比例）報告。

== 工作負載
- *A（真實 session）*：systemd 使用者服務在 ws4 上監測本使用者所有 Claude Code／Codex 行程樹，共 #get(A, "monitor", "trees") 棵樹、#get(A, "monitor", "sessions") 個 session、合計 #f(get(A, "monitor", "tree_hours")) 樹·小時。
- *B（SWE-bench Lite）*：mini-SWE-agent 2.4.6 在 rootless Docker 29.8.2 的官方 SWE-bench 映像中執行，共 #get(B, "runs", "n") 次執行、#get(B, "runs", "instances") 題；模型 #get(B, "runs", "model")，總花費 #f(get(B, "runs", "cost_usd"), d: 2) USD。映像在量測窗外預先拉取。

#include "prereg.typ"

== 與原計畫的偏離
- 報告改用 Typst（使用者要求）；數字全部由 Typst 直接讀 `numbers.json` 排入，無手抄數字。
- B 使用 rootless Docker（計畫中「Docker 可用則用容器」的分支；Docker 依 CSIE 官方流程於本次安裝）。
- 新增 10 Hz 工具期間補樣與 cgroup 指標，僅作次要／敏感度分析（於凍結前登記）。
- vmtouch 週期改為自適應（試跑顯示固定 10 秒在 26 GB、3.5 萬檔的目錄上耗用約 30% 單核）。

= 結果

#figure(
  table(columns: 6, align: (left, center, right, center, right, center),
    [假設], [門檻], [A 值（n）], [A], [B 值（n）], [B],
    [H1 冷比例中位數], [≥ 0.50], [#f(get(H, "H1", "A", "median"), d: 2)（#get(H, "H1", "A", "n")）], [#verdict(get(H, "H1", "A", "holds"))],
      [#f(get(H, "H1", "B", "median"), d: 2)（#get(H, "H1", "B", "n")）], [#verdict(get(H, "H1", "B", "holds"))],
    [H2 等模型長度中位數], [≥ 10 s], [#f(get(H, "H2", "A", "median")) s（#get(H, "H2", "A", "n")）], [#verdict(get(H, "H2", "A", "holds"))],
      [#f(get(H, "H2", "B", "median")) s（#get(H, "H2", "B", "n")）], [#verdict(get(H, "H2", "B", "holds"))],
    [H3 尖峰在工具內比例], [≥ 80%], [#pct(get(H, "H3", "A", "frac_in_tool"))（#get(H, "H3", "A", "n")）], [#verdict(get(H, "H3", "A", "holds"))],
      [#pct(get(H, "H3", "B", "frac_in_tool"))（#get(H, "H3", "B", "n")）], [#verdict(get(H, "H3", "B", "holds"))],
  ),
  caption: [預先登記假設的判定。A＝整棵行程樹；B＝sandbox（容器 cgroup）。n 為區間數（H1、H2）或任務數（H3）。],
) <tab-h>

#include "results.typ"

#let t1row(w, t, p) = {
  let r = get(t, p)
  if r == none { () } else {
    (w, p, str(get(r, "dur_s", "n")),
     f(get(r, "dur_s", "median")) + " / " + f(get(r, "dur_s", "p95")),
     f(get(r, "rss_mib", "median"), d: 0) + " / " + f(get(r, "rss_mib", "p95"), d: 0),
     f(get(r, "pss_mib", "median"), d: 0) + " / " + f(get(r, "pss_mib", "p95"), d: 0),
     f(get(r, "cold", "median"), d: 2) + " / " + f(get(r, "cold", "p95"), d: 2) + " (" + str(get(r, "cold", "n")) + ")")
  }
}
#figure(
  table(columns: 7, align: (left, left, right, right, right, right, right),
    [負載], [階段], [n], [長度 s], [Rss MiB], [Pss MiB], [冷比例 (n)],
    ..for p in ("model_wait", "tool_exec", "user_wait") { t1row("A", get(A, "table1"), p) },
    ..for p in ("model_wait", "tool_exec") { t1row("B sandbox", get(B, "table1_sandbox"), p) },
    ..for p in ("model_wait", "tool_exec") { t1row("B 全部", get(B, "table1_all"), p) },
  ),
  caption: [表 1：各階段的中位數 / p95。Rss、Pss 為區間內 1 Hz 樣本的平均；冷比例取區間結束前最後一個樣本，n 為有效區間數（短於 1 秒的區間多半沒有樣本）。B 全部 = harness + sandbox。],
) <tab-1>

#let drow(lab, d) = if d == none { () } else {
  for v in ("mean", "p95") {
    let r = get(d, v)
    (lab, v, f(get(r, "per_sandbox_gib_base"), d: 2), f(get(r, "per_sandbox_gib_tiered"), d: 2),
     str(get(r, "n_base")), str(get(r, "n_tiered")), "+" + f(get(r, "gain_pct")) + "%")
  }
}
#figure(
  table(columns: 7, align: (left, left, right, right, right, right, right),
    [負載], [供給依據], [每 sandbox GiB], [分層後 GiB], [原台數], [分層後], [增幅],
    ..drow("A 整樹", get(A, "density", "model_wait")),
    ..drow("B harness+sandbox", get(B, "density_all")),
  ),
  caption: [探索性換算：128 GB 主機（保留 10%）可容納的 sandbox 數。假設：各 sandbox 獨立、以每 sandbox 的時間平均或 p95 記憶體配置、只在 ≥ 5 秒的等模型區間把「整個區間都沒被觸碰」的頁（區間結束時的 Rss − Referenced）在區間開始時移出且無成本、慢速層容量不限、不計頁快取與跨 sandbox 共享頁。],
) <tab-density>

= 量測開銷
A 的量測器平均使用 #f(get(A, "overhead", "sampler_cpu_pct")) % 單核 CPU（vmtouch 另 #f(get(A, "overhead", "vmtouch_cpu_pct")) %），每次取樣中位數 #f(get(A, "overhead", "sample_ms", "median")) ms（p95 #f(get(A, "overhead", "sample_ms", "p95")) ms），自身 RSS 約 #f(get(A, "overhead", "sampler_rss_mb", "median")) MiB。微基準（目標行程反覆掃 256 MiB，每條件 #get(N, "bench", "none", "reps") 次 × 20 秒）：每秒讀一次 smaps_rollup 使掃頁吞吐量變化 #f(get(N, "bench", "read", "rel_to_none_pct")) %，再加上每秒 clear_refs 為 #f(get(N, "bench", "clear", "rel_to_none_pct")) %（未量測組的標準差為其平均的 #f(if get(N, "bench", "none") != none { 100 * get(N, "bench", "none", "pages_per_s_sd") / get(N, "bench", "none", "pages_per_s_mean") } else { none }) %）；minor fault 數三組相當（#f(get(N, "bench", "none", "minflt_mean"), d: 0)、#f(get(N, "bench", "read", "minflt_mean"), d: 0)、#f(get(N, "bench", "clear", "minflt_mean"), d: 0)）。在 x86 上清 accessed 位元不會使頁面失效，所以 clear_refs 主要成本是一次頁表走訪與 TLB flush，而非額外 page fault；計畫中預期的「少量額外 page fault」在本機未觀察到。單次 smaps_rollup 讀取中位數 #f(get(N, "bench", "latency_ms", "read", "median"), d: 2) ms、clear_refs #f(get(N, "bench", "latency_ms", "clear_refs", "median"), d: 2) ms（1 GiB 行程）。

= 限制
- *取樣粒度*：工具區間中位數不到 1 秒（表 1），1 Hz 樣本會漏掉多數短工具的尖峰，使 H3 偏低；10 Hz 補樣依賴即時偵測，而 Claude Code 偶爾延遲寫入 tool_use 記錄，補樣可能晚開始。敏感度結果見結果節。
- *Referenced 的語意*：THP 設為 `always`，一個 2 MiB 大頁只要有一個子頁被碰就整頁算 Referenced，冷比例因此偏保守（低估）。共享頁（函式庫、node 執行檔）在每個行程的 Rss 中各算一次，ΣRss 高於實際占用；Pss 欄位供對照。
- *clear 時機*：clear 發生在偵測到轉換之後，區間開頭一小段的觸碰不會被算進 Referenced，冷比例因此略為高估；覆蓋率中位數見 `numbers.json`。
- *頁快取*：clear_refs 只涵蓋已映射的頁，未映射的頁快取無法以無 root 方式量測是否被觸碰（idle page tracking 需 root）；B 的容器 cgroup 只被計入它首次讀入的檔案頁，映像層的頁快取多半記在 Docker daemon 名下。mincore 只給常駐量，不給觸碰與否。
- *代表性*：A 只含單一使用者、單一主機、約兩天、且包含建置本工具的 session；B 的 harness 在容器外，與 harness 在 sandbox 內的部署（如 AgentCgroup）不同，因此 B 另報 harness + sandbox。B 為單一模型、單一 agent 框架。
- *時鐘*：所有時間戳來自同一主機；Claude Code 的 assistant 記錄時間戳是區塊完成時間，user_wait 起點以同一回應最後一個區塊為準。

本報告不推論「agent 很省記憶體」或任何超出上述量測範圍的結論。
