#!/usr/bin/env python3
"""
教育部樂齡學習網・臺南市 — 各中心每月上傳的課程表

  https://moe.senioredu.moe.gov.tw/HomeSon/Tainan/TainanNewsCenter

這頁的表格一次就列出近一年全部的「中心消息」（約 440 則），「下一頁」只是前端切換顯示，
沒有另外的 API；首頁只放最新 10 則。每則公告的課表可能是內文裡的圖片、內文裡的表格，
也可能是附件（PDF、Word、Excel、圖片），全部都存下來。

每次執行：
  1. 抓列表，挑出 SINCE 以後發的、或標題寫 START_YM 以後月份的公告
     （9 月課表常在 8 月底就發，只看發文日期會漏掉）
  2. 還沒抓過的公告才點進去、下載內文圖片和附件，存到 moe/<中心>_<公告日期>/；
     內文有文字（有些中心直接把課表做成網頁表格）就存成 內文.txt
  3. 紀錄寫進 moe/state.json；抓過的公告下次不再抓

每次請求間隔至少 WAIT 秒。下載的檔案不要 commit（moe/ 在 .gitignore 裡）。

用法：
  python3 moe_fetch.py                      # 全部中心
  python3 moe_fetch.py --centers 永康,新化,西港
"""

from __future__ import annotations

import argparse
import html
import json
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

BASE = Path(__file__).resolve().parent
OUT = BASE / "moe"
STATE = OUT / "state.json"
SITE = "https://moe.senioredu.moe.gov.tw"
LIST_URL = SITE + "/HomeSon/Tainan/TainanNewsCenter"
DETAIL_URL = SITE + "/HomeSon/Tainan/TainanCenterMoreNews?seniorCenterMessageFileViewModel.enFormId="

SINCE = "2026-09-01"     # 發文日期在這天以後
START_YM = (2026, 9)     # 或標題寫這個月以後的課表
WAIT = 2.5               # 每次請求之間至少等幾秒
UA = "Mozilla/5.0 (leling-scraper)"

_last = 0.0


def get(url: str) -> bytes:
    global _last
    time.sleep(max(0.0, WAIT - (time.time() - _last)))
    url = urllib.parse.quote(url, safe=":/?&=%_.-~")  # 檔名有中文時
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.read()
    finally:
        _last = time.time()


def _text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>|\s+", " ", s)).strip()


def title_ym(title: str) -> tuple[int, int] | None:
    """「115年11月」「115年 11.12 月」「115年度09月」→ (2026, 11)；寫兩個月時取第一個。"""
    m = re.search(r"(1\d\d)\s*年度?\s*(\d{1,2})", title)
    return (int(m[1]) + 1911, int(m[2])) if m else None


def parse_list(page: str) -> list[dict]:
    i = page.index('id="DataTable"')
    body = page[page.index("<tbody>", i):page.index("</tbody>", i)]
    out = []
    for row in re.findall(r"<tr>(.*?)</tr>", body, re.S):
        fid = re.search(r"enFormId=([^\"&]+)\"", row)
        tds = [_text(x) for x in re.findall(r"<td.*?</td>", re.sub(r"<!--.*?-->", "", row, flags=re.S), re.S)]
        if not fid or len(tds) < 3:
            continue
        out.append({"id": fid[1], "title": tds[0].replace("(另開新視窗)", "").strip(),
                    "date": tds[1].replace("/", "-"), "center": tds[2], "url": DETAIL_URL + fid[1]})
    return out


def wanted(a: dict) -> bool:
    ym = title_ym(a["title"])
    return a["date"] >= SINCE or (ym is not None and ym >= START_YM)


def parse_detail(page: str) -> tuple[str, str, list[dict]]:
    """回傳（內文 HTML, 內文文字, 內文圖片和附件下載連結）。內文的表格一列寫成一行、儲存格用「 | 」隔開。"""
    i = page.index('class="row section-to-print"')
    block = page[i:page.index("公告日期", i)]
    j = block.index('<div class="col-12 text-start px-4">')
    content = block[j:block.rindex('<div class="col-12', 0, block.index('alt="附件下載"'))]
    files = []
    for src in re.findall(r"<img[^>]+src=\"([^\"]+)\"", content):
        if "/UploadFiles/" in src:
            files.append({"kind": "inline", "url": urllib.parse.urljoin(SITE, html.unescape(src)),
                          "name": urllib.parse.unquote(src.rsplit("/", 1)[-1])})
    for m in re.finditer(r"<a[^>]+href=\"([^\"]*/UploadFiles/[^\"]+)\"[^>]*?(?:download=\"([^\"]*)\")?[^>]*>", block):
        files.append({"kind": "attachment", "url": urllib.parse.urljoin(SITE, html.unescape(m[1])),
                      "name": html.unescape(m[2] or urllib.parse.unquote(m[1].rsplit("/", 1)[-1]))})
    return content, content_text(content), files


def content_text(s: str) -> str:
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"<br\s*/?>", " ", s)
    s = re.sub(r"</t[dh]>", " | ", s)
    s = re.sub(r"</(tr|p|div|li)>", "\n", s)
    lines = (re.sub(r" *\| *", " | ", re.sub(r"[ \u3000]+", " ", _text(x))).strip(" |") for x in s.split("\n"))
    return "\n".join(x for x in lines if x)


def safe(name: str) -> str:
    return re.sub(r"[\\/:*?\"<>|\s]+", "_", name).strip("._") or "file"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--centers", default="", help="只抓這些中心（逗號分隔，比對公告單位名稱的一部分）")
    ap.add_argument("--backfill-html", action="store_true", help="抓過、但還沒存 內文.html 的公告重抓內文（不重新下載附件）")
    args = ap.parse_args()
    keys = [k for k in args.centers.split(",") if k.strip()]

    OUT.mkdir(exist_ok=True)
    state = json.loads(STATE.read_text(encoding="utf-8")) if STATE.exists() else {"announcements": {}}
    done = state["announcements"]

    items = [a for a in parse_list(get(LIST_URL).decode("utf-8")) if wanted(a)]
    if keys:
        items = [a for a in items if any(k in a["center"] for k in keys)]
    if args.backfill_html:
        for a in items:
            d = done.get(a["id"])
            if d and d.get("has_text") and not (OUT / d["folder"] / "內文.html").exists():
                content, _, _ = parse_detail(get(a["url"]).decode("utf-8"))
                (OUT / d["folder"] / "內文.html").write_text(content, encoding="utf-8")
                print(f"  補存 {d['folder']}/內文.html")
        return
    todo = [a for a in items if a["id"] not in done]
    print(f"列表：符合條件 {len(items)} 則，其中 {len(todo)} 則還沒抓")

    for a in todo:
        content, text, files = parse_detail(get(a["url"]).decode("utf-8"))
        folder = f"{a['center']}_{a['date'].replace('-', '')}"
        n = 2
        while any(d.get("folder") == folder for d in done.values()):
            folder = f"{a['center']}_{a['date'].replace('-', '')}_{n}"
            n += 1
        (OUT / folder).mkdir(exist_ok=True)
        if text:
            (OUT / folder / "內文.txt").write_text(text + "\n", encoding="utf-8")
            # 原始 HTML：網頁表格的合併儲存格（rowspan、colspan）由 leling-courses/moe_tables.py 讀
            (OUT / folder / "內文.html").write_text(content, encoding="utf-8")
        for k, f in enumerate(files, 1):
            path = OUT / folder / f"{k}_{safe(f['name'])}"
            path.write_bytes(get(f["url"]))
            f["path"] = str(path.relative_to(BASE))
        done[a["id"]] = {**a, "folder": folder, "files": files, "has_text": bool(text),
                         "fetched_at": datetime.now().astimezone().isoformat(timespec="seconds")}
        STATE.write_text(json.dumps(state, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  {a['date']} {a['title']}：{len(files)} 個檔案" + ("，內文有文字" if text else "")
              + ("（沒有圖、附件或文字）" if not files and not text else ""))


if __name__ == "__main__":
    main()
