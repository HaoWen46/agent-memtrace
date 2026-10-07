// Shared by report.typ, results.typ, summary.typ. Every number is read from numbers.json,
// which memtrace/analyze.py recomputes from raw/.
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
// Epoch seconds -> Asia/Taipei (UTC+8, no DST) as "YYYY-MM-DD HH:MM".
#let tpe(t) = (datetime(year: 1970, month: 1, day: 1, hour: 0, minute: 0, second: 0)
  + duration(seconds: int(calc.floor(float(t))) + 8 * 3600)).display("[year]-[month]-[day] [hour]:[minute]")
