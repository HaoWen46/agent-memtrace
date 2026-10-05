#import "lib.typ": *
// 150-character summary for the study plan. Numbers and the verdict-dependent clause come from numbers.json.
#let h2short = get(H, "H2", "A", "holds") == false and get(H, "H2", "B", "holds") == false
#block(stroke: (left: 2pt + rgb("#2a78d6")), inset: (left: 8pt, y: 4pt))[
  *摘要*　以無需 root 的 /proc 取樣與 clear_refs，量測 coding agent 行程樹在等模型、執行工具、等使用者三階段的記憶體。在真實 Claude Code session 與 30 次 SWE-bench Lite 執行中，≥ 5 秒等模型區間結束時未被觸碰的記憶體比例中位數為 #pct(get(H, "H1", "A", "median"))（整棵行程樹）與 #pct(get(H, "H1", "B", "median"))（SWE-bench sandbox）；等模型區間長度中位數為 #f(get(H, "H2", "A", "median")) 與 #f(get(H, "H2", "B", "median")) 秒，SWE-bench 有 #pct(get(H, "H3", "B", "frac_in_tool")) 的記憶體尖峰發生在工具執行期間。#if h2short [冷記憶體比例高，但過半的等模型區間短於 10 秒。]
]
