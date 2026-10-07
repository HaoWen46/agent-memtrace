"""Write Fig 1 as SVG from the same matplotlib figure object that memtrace/analyze.py saves as PDF.

Runs analyze.main() unchanged with every output redirected to OUT (raw/ is still read from the repo), and
hooks Figure.savefig so fig1_timeline.pdf is also saved as fig1_timeline.svg. Used 2026-10-08 to make
report/fig/fig1_timeline.svg; OUT/numbers.json matched the committed numbers.json except meta, and
OUT's fig1_timeline.pdf rendered pixel-identical to report/fig/fig1_timeline.pdf.
Usage: uv run python report/fig1_svg.py OUT_DIR
"""
import pathlib
import shutil
import sys

import matplotlib.figure as mf

import memtrace.analyze as an

T = pathlib.Path(sys.argv[1])
(T / "report" / "fig").mkdir(parents=True)
(T / "results").mkdir()
shutil.copy("report/prereg.typ", T / "report" / "prereg.typ")
an.ROOT, an.RES, an.FIG = T, T / "results", T / "report" / "fig"
orig = mf.Figure.savefig


def savefig(self, path, *a, **k):
    orig(self, path, *a, **k)
    if str(path).endswith("fig1_timeline.pdf"):
        orig(self, str(path)[:-4] + ".svg", *a, **k)


mf.Figure.savefig = savefig
an.main()
