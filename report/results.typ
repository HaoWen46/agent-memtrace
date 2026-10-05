#import "lib.typ": *
// Result prose and figures. Draft prose is filled after each analysis run; numbers come from numbers.json.
#figure(image("fig/fig1_timeline.pdf", width: 100%), caption: [單一任務時間線。實線為整樹（A）或 harness + sandbox（B）的 Rss，虛線為自本階段開始以來被觸碰過的頁（Referenced）；底色為階段。短於圖寬 0.4% 的工具區間為了可見而加寬。]) <fig-1>
#figure(image("fig/fig2_cold_cdf.pdf", width: 100%), caption: [等模型區間結束時的冷比例（1 − Referenced/Rss）CDF，依區間長度分組；點線為 H1 門檻 0.5。]) <fig-2>
#figure(image("fig/fig3_peaks.pdf", width: 100%), caption: [每個任務的尖峰／平均 Rss 比，顏色為尖峰樣本所在階段（1 Hz primary 樣本）。]) <fig-3>
#figure(image("fig/fig4_tool_io.pdf", width: 100%), caption: [每次工具呼叫前後（以最近的樣本夾住）的讀寫位元組 CDF；0 位元組的比例標在圖例中。syscall = rchar/wchar，storage = read_bytes/write_bytes（A）或 cgroup io.stat（B）。]) <fig-4>
