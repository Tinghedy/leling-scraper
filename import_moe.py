#!/usr/bin/env python3
"""
把教育部樂齡學習網的課程（leling-courses 程式讀表格、AI 讀圖片後整理好的結果）轉成 data/courses.json，
當作 app.py 的課程資料。不呼叫任何模型。

輸入：../leling-courses/output/review/data.json 裡「來源」是教育部的課（教育部樂齡學習網、教育部＋FB）
輸出：data/courses.json，欄位和 extract.py 相同（FIELDS），source_type = "moe_doc"

去識別化：講師一律空值；課名、地點裡的講師姓名、電話、「某某老師」拿掉。FB 貼文連結不帶過來，
source_url 只放教育部公告網址。
domain 用 run_daily.py 的 TOPICS 關鍵字比對課名，比對不到填 null。
inferred_fields：AI 讀圖時推測的課名、地點，以及「只寫星期，照月份展開」的日期。

用法：python3 import_moe.py
"""

from __future__ import annotations

import csv
import json
import re
from pathlib import Path

from extract import FIELDS, WEEKDAYS, scrub, time_slot

BASE = Path(__file__).resolve().parent
SOURCE = Path.home() / "Downloads/leling-courses/output/review/data.json"
CENTERS = BASE / "centers.csv"
OUT = BASE / "data" / "courses.json"
MOE_SOURCES = {"教育部樂齡學習網", "教育部＋FB"}

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


def to_record(c: dict, post: dict, center: dict, others: set[str]) -> dict:
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
        "start_date": c.get("第一堂") or None, "end_date": c.get("最後一堂") or None,
        "venue": clean(c.get("地點"), names), "domain": domain_of(name), "instructor": None,
        "inferred_fields": sorted(inferred), "source_url": post.get("link") or None,
        "source_type": "moe_doc", "post_date": (c.get("發文時間") or "")[:10] or None,
    }
    return {k: rec[k] for k in FIELDS}


def main() -> None:
    data = json.loads(SOURCE.read_text(encoding="utf-8"))
    with CENTERS.open(encoding="utf-8-sig", newline="") as f:
        centers = {r["name"]: r for r in csv.DictReader(f)}
    courses = [c for c in data["courses"] if c["來源"] in MOE_SOURCES]
    others = known_names(data["courses"])
    records = [to_record(c, data["posts"].get(c["post"], {}), centers.get(c["中心"], {}), others) for c in courses]
    dropped = sum(not r["course_name"] for r in records)  # 課名整個是人名或空白
    records = [r for r in records if r["course_name"]]
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    missing = sorted({r["center"] for r in records if r["lat"] is None})
    print(f"{len(records)} 門課（課名去掉人名後是空的，跳過 {dropped} 門），{len({r['center'] for r in records})} 間中心 → data/courses.json")
    print(f"沒寫星期 {sum(not r['weekday'] for r in records)} 門、沒寫開始時間 {sum(not r['start_time'] for r in records)} 門、"
          f"有 inferred 欄位 {sum(bool(r['inferred_fields']) for r in records)} 門、domain 比對不到 "
          f"{sum(r['domain'] is None for r in records)} 門")
    if missing:
        print("centers.csv 沒有座標的中心：" + "、".join(missing))


if __name__ == "__main__":
    main()
