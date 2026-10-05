#import "lib.typ": *
// Result prose. Every number is an expression over numbers.json; wording that depends on a verdict uses verdict().
#let h1a = get(H, "H1", "A")
#let h1b = get(H, "H1", "B")
#let pk = get(B, "peaks")

== 等模型期間的冷記憶體（H1）
A 的整棵行程樹在長度 ≥ 5 秒的等模型區間結束時，冷比例中位數為 #f(get(h1a, "median"), d: 2)（n = #get(h1a, "n")，p95 #f(get(h1a, "p95"), d: 2)），H1 在 A #verdict(get(h1a, "holds"))。B 的 sandbox 中位數為 #f(get(h1b, "median"), d: 2)（n = #get(h1b, "n")），H1 在 B #verdict(get(h1b, "holds"))；但這些區間結束時 sandbox 的 Rss 中位數只有 #f(get(h1b, "sandbox_rss_end_mib", "median")) MiB（p95 #f(get(h1b, "sandbox_rss_end_mib", "p95")) MiB）——沒有工具在跑時，sandbox 只剩容器 init 與 agent 留在背景的行程，比例高但絕對量小。容器外的 harness 在同一時刻約 #f(get(B, "H1_secondary", "harness_rss_end_mib", "median"), d: 0) MiB，冷比例中位數 #f(get(B, "H1_secondary", "harness", "median"), d: 2)；harness + sandbox 合計 #f(get(B, "H1_secondary", "harness_plus_sandbox", "median"), d: 2)。clear 之後到區間結束的覆蓋率中位數：A #pct(get(h1a, "coverage", "median"))、B #pct(get(h1b, "coverage", "median"))。冷比例與區間長度的關係見 @fig-2。

== 等模型區間長度（H2）與預取窗口
等模型區間長度中位數：A #f(get(H, "H2", "A", "median")) 秒（n = #get(H, "H2", "A", "n")，p95 #f(get(H, "H2", "A", "p95")) 秒），B #f(get(H, "H2", "B", "median")) 秒（n = #get(H, "H2", "B", "n")，p95 #f(get(H, "H2", "B", "p95")) 秒）。H2 在 A #verdict(get(H, "H2", "A", "holds"))、在 B #verdict(get(H, "H2", "B", "holds"))。B 使用回應快的 flash 等級模型，每次呼叫通常只有數秒；可用於分層與預取的窗口因此主要落在少數長區間的尾部，而不是典型區間。

== 尖峰位置（H3）與大小
B 有 #pct(get(H, "H3", "B", "frac_in_tool")) 的執行（n = #get(H, "H3", "B", "n")）其 sandbox 尖峰落在工具區間內，H3 在 B #verdict(get(H, "H3", "B", "holds"))；加入 10 Hz 補樣為 #pct(get(B, "H3_sensitivity", "with_burst"))，以 cgroup `memory.current` 計為 #pct(get(B, "H3_sensitivity", "cgroup_memory_current"))。A 只有 #get(H, "H3", "A", "n") 個符合條件的使用者回合，比例 #pct(get(H, "H3", "A", "frac_in_tool"))（補樣版本 #pct(get(A, "H3_sensitivity", "with_burst"))），H3 在 A #verdict(get(H, "H3", "A", "holds"))。

B 每次執行的 sandbox 尖峰（cgroup `memory.current`）中位數 #f(get(pk, "cg_current_gib", "median"), d: 2) GiB、p95 #f(get(pk, "cg_current_gib", "p95"), d: 1) GiB、最大 #f(get(pk, "cg_current_gib", "max"), d: 1) GiB。#get(pk, "runs_over_100_procs") 次執行在 sandbox 內同時有超過 100 個行程（最多 #f(get(pk, "max_procs", "max"), d: 0) 個）：Django 的測試執行器預設依可見 CPU 數開 worker，而容器沒有 CPU 限制、看得到主機的 144 核；這類尖峰的大小因此取決於主機核心數，不能直接外推到有 CPU 配額的 sandbox。fork 出的 worker 共享頁面，ΣRss 因而重複計算（尖峰中位數 ΣRss #f(get(pk, "rss_gib", "median"), d: 2) GiB，p95 #f(get(pk, "rss_gib", "p95"), d: 1) GiB，對照 Pss p95 #f(get(pk, "pss_gib", "p95"), d: 1) GiB），所以 @fig-3 改用 Pss 畫尖峰／平均比；H3 判定仍依預先登記用 Rss；改用 Pss 時比例為 B #pct(get(B, "H3_sensitivity", "pss"))、A #pct(get(A, "H3_sensitivity", "pss"))。

== 各階段特徵、檔案快取與 I/O
工具區間很短：中位數 A #f(get(A, "phases", "tool_exec", "median"), d: 2) 秒、B #f(get(B, "phases", "tool_exec", "median"), d: 2) 秒（@tab-1）。工作目錄的頁幾乎全部常駐於頁快取：B 的 `/testbed`（中位數 #f(get(B, "mincore_testbed", "size_mib", "median"), d: 0) MiB）常駐比例中位數 #pct(get(B, "mincore_testbed", "resident_frac", "median"))。因此每次工具呼叫的儲存端讀取多半為零：B 的 sandbox cgroup 有 #pct(get(B, "tool_io", "cg_rbytes", "frac_zero")) 的工具呼叫沒有任何 block 讀取，A 為 #pct(get(A, "tool_io", "read_bytes", "frac_zero"))；A 的 syscall 層讀取中位數則為 #f(get(A, "tool_io", "rchar", "median") / 1024, d: 0) KiB（@fig-4）。

#let rep = get(B, "repeats")
#if rep != none [
  #figure(
    table(columns: 6, align: (left, center, right, right, right, right),
      [題目], [次], [長度 s], [尖峰 GiB], [等模型中位數 s], [冷比例中位數],
      ..for (iid, r) in rep {
        for i in range(r.duration_s.len()) {
          (text(size: 8pt, iid), str(i), f(r.duration_s.at(i), d: 0), f(r.peak_cg_gib.at(i), d: 2),
           f(r.model_wait_median_s.at(i)), f(r.cold_median.at(i), d: 2))
        }
      },
    ),
    caption: [B 重跑變異：同一題的三次執行。尖峰為 sandbox cgroup `memory.current`；冷比例為 ≥ 5 秒等模型區間的中位數。],
  ) <tab-rep>
]

== 128 GB 主機換算（探索性）
在 @tab-density 的假設下，把 ≥ 5 秒等模型區間中整段未被觸碰的頁移到慢速層，以時間平均配置計，A 每台可多放 #f(get(A, "density", "model_wait", "mean", "gain_pct")) %、B（harness + sandbox）多放 #f(get(B, "density_all", "mean", "gain_pct")) %；以 p95 配置計分別為 #f(get(A, "density", "model_wait", "p95", "gain_pct")) % 與 #f(get(B, "density_all", "p95", "gain_pct")) %。增幅小的主因是可分層的時間少：符合條件（≥ 5 秒且有有效樣本）的等模型區間只占 A 監測時間的 #pct(get(A, "density", "model_wait", "frac_time_tiered")) 與 B 執行時間的 #pct(get(B, "density_all", "frac_time_tiered"))；A 的大部分時間是等使用者，依計畫不計入（若一併計入等使用者區間，A 的時間平均增幅為 #f(get(A, "density", "model_and_user_wait", "mean", "gain_pct")) %）。

#figure(image("fig/fig1_timeline.pdf", width: 100%), caption: [單一任務時間線。實線為整樹（A）或 harness + sandbox（B）的 Rss，虛線為自本階段開始以來被觸碰過的頁（Referenced）；底色為階段。短於圖寬 0.4% 的工具區間為了可見而加寬。]) <fig-1>
#figure(image("fig/fig2_cold_cdf.pdf", width: 100%), caption: [等模型區間結束時的冷比例 CDF，依區間長度分組；點線為 H1 門檻 0.5。]) <fig-2>
#figure(image("fig/fig3_peaks.pdf", width: 100%), caption: [每個任務的尖峰／平均 Pss 比，顏色為尖峰樣本所在階段（1 Hz primary 樣本）。]) <fig-3>
#figure(image("fig/fig4_tool_io.pdf", width: 100%), caption: [每次工具呼叫前後（以最近的樣本夾住）的讀寫位元組 CDF；0 位元組的比例標在圖例中。syscall = rchar/wchar，storage = read_bytes/write_bytes（A）或 cgroup io.stat（B）。]) <fig-4>
