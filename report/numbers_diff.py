"""Pair every number in the body text of two report PDFs (draft vs final) and print a markdown table.

Usage: uv run --no-project --with pymupdf python report/numbers_diff.py DRAFT.pdf FINAL.pdf > report/NUMBERS-DIFF.md

Body text = everything Typst set (text, tables, captions, summary, pre-registration). Excluded: text inside
the matplotlib figures (font DejaVu Sans, not Mono), page numbers, heading numbers, figure/table labels
("表 1", "圖 2"), and digits inside identifiers (p95, H1, x86, django-13158, hex hashes). Numbers are paired
by aligning the two texts on their non-number tokens.
"""
import difflib
import re
import sys

import pymupdf

NUM = re.compile(r"\d{4}-\d{2}-\d{2}(?: \d{2}:\d{2}(?::\d{2})?)?"
                 r"|(?<![A-Za-z_\-/.\d:])\d{2}:\d{2}(?::\d{2})?(?![A-Za-z_\d])"
                 r"|(?<![A-Za-z_\-/.\d:])\d+(?:\.\d+)*(?: ?%)?(?![A-Za-z_\d])")
HEAD = re.compile(r"^(\d+(?:\.\d+)*)\.\s*(.*)$")


def is_figure_font(font):
    return font.startswith("DejaVuSans") and "Mono" not in font


def tokens(path):
    """[(kind, text, page, section, context)] with kind 'n' for numbers, 'w' for other tokens."""
    out, section, flat = [], "首頁", ""
    for pno, page in enumerate(pymupdf.open(path), 1):
        for block in page.get_text("dict")["blocks"]:
            for line in block.get("lines", []):
                spans = [s for s in line["spans"] if not is_figure_font(s["font"])]
                text = "".join(s["text"] for s in spans).strip()
                if not text or (text.isdigit() and line["bbox"][1] > page.rect.height - 60):
                    continue  # empty, figure-only, or page number
                m = HEAD.match(text)
                if m and "Bold" in spans[0]["font"]:
                    section = f"§{m[1]} {m[2]}"
                    text = m[2]
                pos = 0
                for nm in NUM.finditer(text):
                    pre = text[pos:nm.start()]
                    for w in re.findall(r"[A-Za-z_]+|\S", pre):
                        out.append(("w", w, pno, section, ""))
                    flat += pre
                    label = re.search(r"[表圖]\s*$", flat)
                    val = nm.group().replace(" %", "%")
                    if label:
                        out.append(("w", val, pno, section, ""))
                    else:
                        out.append(("n", val, pno, section, flat[-16:].replace("\n", " ")))
                    flat += nm.group()
                    pos = nm.end()
                rest = text[pos:]
                for w in re.findall(r"[A-Za-z_]+|\S", rest):
                    out.append(("w", w, pno, section, ""))
                flat += rest + " "
    return out


def cell(s):
    return s.replace("|", "\\|")


def main():
    a, b = tokens(sys.argv[1]), tokens(sys.argv[2])
    ka = [t[1] if t[0] == "w" else "<N>" for t in a]
    kb = [t[1] if t[0] == "w" else "<N>" for t in b]
    rows = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(None, ka, kb, autojunk=False).get_opcodes():
        na = [t for t in a[i1:i2] if t[0] == "n"]
        nb = [t for t in b[j1:j2] if t[0] == "n"]
        if len(na) == len(nb):
            rows += list(zip(na, nb))
        else:
            rows += [(t, None) for t in na] + [(None, t) for t in nb]
    n_same = n_chg = n_add = n_del = 0
    lines = []
    for ta, tb in rows:
        ref = tb or ta
        loc = f"p.{ref[2]}" + (f"（草稿 p.{ta[2]}）" if ta and tb and ta[2] != tb[2] else "") + f" {ref[3]} · …{ref[4]}▸"
        if ta and tb:
            if ta[1] == tb[1]:
                n_same += 1
                final = f"{tb[1]}（不變）"
            else:
                n_chg += 1
                final = f"**{tb[1]}**"
            lines.append(f"| {cell(loc)} | {ta[1]} | {final} |")
        elif ta:
            n_del += 1
            lines.append(f"| {cell(loc)} | {ta[1]} | —（刪除） |")
        else:
            n_add += 1
            lines.append(f"| {cell(loc)} | —（新增） | **{tb[1]}** |")
    print("# NUMBERS-DIFF：草稿（2026-10-06）→ 最終版（2026-10-08）\n")
    print(f"由 `report/numbers_diff.py` 比對 `{sys.argv[1]}` 與 `{sys.argv[2]}` 的正文產生。"
          "範圍：正文、表格、圖說、摘要與預先登記；不含圖內文字（座標、圖例；圖 1–4 已依最終資料重繪）、頁碼、章節編號、圖表標號與識別碼內的數字。"
          "位置以最終版頁碼為準，「…▸」後為該數字之前的文字。\n")
    print(f"合計 {len(rows)} 個數字：變更 {n_chg}、不變 {n_same}、新增 {n_add}、刪除 {n_del}。\n")
    ids = [re.search(r"git (\w+) · 預先登記 sha256 (\w+)", pymupdf.open(p)[0].get_text()) for p in sys.argv[1:3]]
    print(f"首頁標頭的識別碼（非數字，不列入上表）：git commit {ids[0][1]} → {ids[1][1]}；"
          f"預先登記 sha256 前綴 {ids[0][2]} → {ids[1][2]}" + ("（不變）" if ids[0][2] == ids[1][2] else "") + "。\n")
    print("| 位置（頁／節） | 草稿值 | 最終值 |\n|---|---|---|")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
