#!/usr/bin/env python3
"""
把 leling-courses 整理好的完整課程資料庫（教育部樂齡學習網＋FB）轉成 data/courses.json，
當作 app.py 的課程資料。只做欄位轉換，不呼叫任何模型。

輸入：../leling-courses/output/樂齡課程.xlsx 的「課程」工作表（一列一門課）
輸出：data/courses.json，欄位是 extract.py 的 FIELDS 再加 dates
  dates        每一堂的日期（「上課日期」欄，YYYY-MM-DD 的清單）；原文沒寫日期的是 []
  source_type  「來源」有教育部的 → moe_doc；「僅 FB」→ fb_post
  source_url   「貼文連結」：教育部公告網址，或 FB 貼文網址（教育部＋FB 的課只留教育部的）

去識別化：講師一律空值；課名、地點裡的講師姓名、電話、「某某老師」拿掉；不帶發文者、圖片路徑。
「（整則貼文：沒有抽出課程）」這類複查台的佔位列不是課，不轉進來。
domain 用 run_daily.py 的 TOPICS 關鍵字比對課名，比對不到填 null。
inferred_fields：AI 讀圖時推測的課名、地點，以及「只寫星期，照月份展開」的日期。

用法：uv run --python 3.12 --with-requirements requirements.txt python import_courses.py
"""

from __future__ import annotations

import csv
import json
import re
from datetime import datetime
from pathlib import Path

from extract import FIELDS, WEEKDAYS, scrub, time_slot

OUT_FIELDS = FIELDS[:FIELDS.index("end_date") + 1] + ["dates"] + FIELDS[FIELDS.index("end_date") + 1:]

BASE = Path(__file__).resolve().parent
SOURCE = Path.home() / "Downloads/leling-courses/output/樂齡課程.xlsx"
SHEET = "課程"
PLACEHOLDER = "（整則貼文"
CENTERS = BASE / "centers.csv"
OUT = BASE / "data" / "courses.json"

# 和 run_daily.py 的 TOPICS 相同（run_daily.py 不 import，避免執行它的設定）
TOPICS = {
    "數位科技": ["手機", "智慧型手機", "平板", "電腦", "網路", "LINE", "line", "APP", "app",
              "數位", "AI", "拍照", "視訊", "詐騙", "Google", "YouTube"],
    "健康運動": ["健康", "運動", "體適能", "肌力", "伸展", "瑜珈", "太極", "舞蹈", "律動",
              "營養", "防跌", "失智", "預防"],
    "藝術手作": ["手作", "繪畫", "書法", "藝術", "編織", "拼布", "彩繪", "花藝", "陶", "DIY"],
    "音樂表演": ["歌唱", "唱歌", "音樂", "樂器", "烏克麗麗", "陶笛", "戲劇", "表演"],
    "語言文化": ["台語", "臺語", "英語", "英文", "日語", "日文", "閩南語", "文化", "歷史", "導覽"],
    "生活財務": ["理財", "法律", "安全", "防災", "交通", "消費", "長照", "退休"],
    "社區志工": ["志工", "社區", "代間", "共餐", "關懷"],
}
AI_FIELDS = {"課名": "course_name", "地點": "venue"}


def domain_of(name: str) -> str | None:
    for domain, words in TOPICS.items():
        if any(w in name for w in words):
            return domain
    return None


def known_names(courses: list[dict]) -> set[str]:
    """所有課的講師欄裡 3 個中文字的值，當作姓名（講師欄有時寫的是「音樂」「延緩失能」這種，不算）。"""
    out = set()
    for c in courses:
        out |= {n for n in re.split(r"[、,，\s]", c.get("講師") or "") if re.fullmatch(r"[一-鿿]{3}", n)}
    return out


def clean(v: str | None, names: list[str] | set[str]) -> str | None:
    """拿掉講師姓名，再用 extract.scrub 拿掉電話和「某某老師」。"""
    for n in names:
        if len(n) >= 2 and v:
            v = v.replace(n, "")
    return scrub(v)


def day(v) -> str | None:
    return v.strftime("%Y-%m-%d") if isinstance(v, datetime) else (str(v)[:10] if v else None)


def read_sheet() -> list[dict]:
    from openpyxl import load_workbook  # 只有轉檔時需要
    rows = load_workbook(SOURCE, read_only=True)[SHEET].iter_rows(values_only=True)
    head = next(rows)
    return [dict(zip(head, r)) for r in rows if any(r)]


def to_record(c: dict, center: dict, others: set[str]) -> dict:
    names = [n.strip() for n in (c.get("講師") or "").replace("、", ",").split(",") if n.strip()] + sorted(others)
    inferred = {AI_FIELDS[f] for f in (c.get("AI 推測的欄位") or "").split("、") if f in AI_FIELDS}
    if "照" in (c.get("推定") or "") and "展開" in c["推定"]:
        inferred |= {"start_date", "end_date"}
    lat, lng = center.get("lat"), center.get("lng")
    name = clean(c["課名"], names) or ""
    rec = {
        "center": c["中心"], "district": center.get("district") or None,
        "lat": float(lat) if lat else None, "lng": float(lng) if lng else None,
        "course_name": name, "weekday": [w for w in WEEKDAYS if w in (c.get("星期") or "")],
        "start_time": c.get("開始時間") or None, "end_time": c.get("結束時間") or None,
        "time_slot": time_slot(c.get("開始時間")),
        "start_date": day(c.get("第一堂")), "end_date": day(c.get("最後一堂")),
        "dates": [d for d in (c.get("上課日期") or "").split("、") if d],
        "venue": clean(c.get("地點"), names), "domain": domain_of(name), "instructor": None,
        "inferred_fields": sorted(inferred), "source_url": c.get("貼文連結") or None,
        "source_type": "moe_doc" if "教育部" in (c.get("來源") or "") else "fb_post",
        "post_date": day(c.get("發文時間")),
    }
    return {k: rec[k] for k in OUT_FIELDS}


def main() -> None:
    courses = read_sheet()
    with CENTERS.open(encoding="utf-8-sig", newline="") as f:
        centers = {r["name"]: r for r in csv.DictReader(f)}
    others = known_names(courses)
    # 「（整則貼文：沒有抽出課程）」是複查台用的佔位列，不是課
    placeholders = sum((c.get("課名") or "").startswith(PLACEHOLDER) for c in courses)
    courses = [c for c in courses if not (c.get("課名") or "").startswith(PLACEHOLDER)]
    records = [to_record(c, centers.get(c["中心"], {}), others) for c in courses]
    dropped = sum(not r["course_name"] for r in records)  # 課名整個是人名或空白
    records = [r for r in records if r["course_name"]]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    missing = sorted({r["center"] for r in records if r["lat"] is None})
    print(f"{len(records)} 門課（佔位列跳過 {placeholders} 列；課名去掉人名後是空的，跳過 {dropped} 門），{len({r['center'] for r in records})} 間中心 → data/courses.json")
    print("來源：" + "、".join(f"{k} {sum(r['source_type'] == k for r in records)}" for k in ("moe_doc", "fb_post")))
    print(f"沒寫星期 {sum(not r['weekday'] for r in records)} 門、沒寫開始時間 {sum(not r['start_time'] for r in records)} 門、"
          f"有 inferred 欄位 {sum(bool(r['inferred_fields']) for r in records)} 門、domain 比對不到 "
          f"{sum(r['domain'] is None for r in records)} 門")
    if missing:
        print("centers.csv 沒有座標的中心：" + "、".join(missing))


if __name__ == "__main__":
    main()
