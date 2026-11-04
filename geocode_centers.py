#!/usr/bin/env python3
"""
centers.csv 補上 district、lat、lng、geo_approx 四欄。

  district     從中心名稱取出（「東區樂齡學習中心」→ 東區）；名稱沒有區名的看地址
  lat / lng    用中心地址查 Nominatim（OpenStreetMap）；每秒最多 1 次，帶 User-Agent
  geo_approx   1 = 地址查不到，先填該行政區的中心點，請人工確認

地址來源：教育部樂齡學習網「臺南市轄屬樂齡學習中心聯繫資訊一覽表」（110 年度，
https://moe.senioredu.moe.gov.tw/Home/senior_center_pdf/110/110tainam.pdf）。
之後有中心搬家，改下面 ADDRESSES 再重跑。查過的結果快取在 data/geocode_cache.json。

用法：python3 geocode_centers.py
"""

from __future__ import annotations

import csv
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

BASE = Path(__file__).resolve().parent
CENTERS = BASE / "centers.csv"
CACHE = BASE / "data" / "geocode_cache.json"
NOMINATIM = "https://nominatim.openstreetmap.org/search"
UA = "leling-scraper/1.0 (Tainan senior-learning course map; https://github.com/Tinghedy/leling-scraper)"
# 臺南市的範圍，查到範圍外的結果不算
LAT, LNG = (22.85, 23.42), (120.0, 120.67)

ADDRESSES = {
    "中西區樂齡學習中心": "臺南市中西區民權路3段370號",
    "南區樂齡學習中心": "臺南市南區喜樹路133號",
    "北區樂齡學習中心": "臺南市北區公園路750號",
    "安平區樂齡學習中心": "臺南市安平區慶平路687號",
    "永康區樂齡學習中心": "臺南市永康區華興街2號",
    "歸仁區樂齡學習中心": "臺南市歸仁區文化街2段136號",
    "新化區樂齡學習中心": "臺南市新化區正新路97號",
    "南化區樂齡學習中心": "臺南市南化區南化里172-1號",
    "仁德區樂齡學習中心": "臺南市仁德區中正路二段209號",
    "官田區樂齡學習中心": "臺南市官田區官田里11號",
    "佳里區樂齡學習中心": "臺南市佳里區信義一街336號",
    "西港區樂齡學習中心": "臺南市西港區營西里3號",
    "七股區樂齡學習中心": "臺南市七股區大埕里395號",
    "將軍區樂齡學習中心": "臺南市將軍區將貴里58號",
    "學甲區樂齡學習中心": "臺南市學甲區宅港里1鄰13號",
    "柳營區樂齡學習中心": "臺南市柳營區中山東路二段956號",
    "善化區樂齡學習中心": "臺南市善化區溪美里71號",
    "安定區南興樂齡學習中心": "臺南市安定區六嘉里30號",
    "大內區樂齡學習中心": "臺南市大內區大內里63號",
    "白河區樂齡學習中心": "臺南市白河區竹門里10鄰113號",
    "左鎮區樂齡學習中心": "臺南市左鎮區中正里7號",
    "後壁區樂齡學習中心": "臺南市後壁區長安里5號",
    "關廟區樂齡學習中心": "臺南市關廟區中山路二段172號",
    "山上區樂齡學習中心": "臺南市山上區山上里130號",
    "楠西區樂齡學習中心": "臺南市楠西區中興路107號",
    "新市區樂齡學習中心": "臺南市新市區中興街1號",
    "北門區樂齡學習中心": "臺南市北門區錦湖里75號",
    "東區樂齡學習中心": "臺南市東區勝利路41號",
    "鹽水區樂齡學習中心": "臺南市鹽水區三福路63號",
    "下營區樂齡學習中心": "臺南市下營區中山路二段122巷9號",
    "玉井區樂齡學習中心": "臺南市玉井區中正路1號",
    "龍崎區樂齡學習中心": "臺南市龍崎區崎頂里新市子33號",
    "新營區樂齡學習中心": "臺南市新營區民族路21-61號",
    "樂齡學習示範中心": "臺南市麻豆區安東里113號",
    "大內區環湖樂齡學習中心(優先區)": "臺南市大內區環湖里56號",
    # 北區鎮北樂齡學習中心(優先區)：110 年度名冊裡沒有，用行政區中心點
}

_last = 0.0


def nominatim(q: str, cache: dict) -> dict | None:
    """查一個地址；查到臺南市範圍內的結果才回傳。結果（含查不到）都存進快取。"""
    global _last
    if q in cache:
        return cache[q]
    time.sleep(max(0.0, 1.1 - (time.time() - _last)))
    url = NOMINATIM + "?" + urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "tw"})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept-Language": "zh-TW"})
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            hits = json.loads(r.read().decode("utf-8"))
    finally:
        _last = time.time()
    hit = None
    if hits:
        lat, lng = float(hits[0]["lat"]), float(hits[0]["lon"])
        if LAT[0] <= lat <= LAT[1] and LNG[0] <= lng <= LNG[1]:
            hit = {"lat": round(lat, 6), "lng": round(lng, 6), "name": hits[0].get("display_name", "")}
    cache[q] = hit
    CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")
    return hit


def variants(addr: str) -> list[tuple[str, str]]:
    """由精確到粗略：完整地址 → 去掉「X鄰」→ 只到路名或村里。"""
    out = [("地址", addr)]
    no_lin = re.sub(r"\d+鄰", "", addr)
    if no_lin != addr:
        out.append(("地址", no_lin))
    street = re.sub(r"[\d\-之]+號$", "", no_lin)
    street = re.sub(r"\d+巷$", "", street)
    if street != no_lin:
        out.append(("路名或村里", street))
    return out


def district_of(name: str, addr: str) -> str:
    m = re.match(r"(.{1,3}?區)", name)
    if m:
        return m.group(1)
    m = re.search(r"臺南市(.{1,3}?區)", addr)
    return m.group(1) if m else ""


def main() -> None:
    CACHE.parent.mkdir(exist_ok=True)
    cache = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    with CENTERS.open(encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    base_cols = [c for c in rows[0].keys() if c not in ("district", "lat", "lng", "geo_approx")]
    approx, levels = [], {}
    for r in rows:
        addr = ADDRESSES.get(r["name"], "")
        r["district"] = district_of(r["name"], addr)
        hit, level = None, ""
        for level, q in variants(addr) if addr else []:
            hit = nominatim(q, cache)
            if hit:
                break
        if not hit:
            hit, level = nominatim(f"臺南市{r['district']}", cache), "行政區中心點"
        r["lat"], r["lng"] = (hit["lat"], hit["lng"]) if hit else ("", "")
        r["geo_approx"] = "1" if level == "行政區中心點" or not hit else ""
        levels[r["name"]] = level if hit else "查不到"
        if r["geo_approx"]:
            approx.append(r["name"])
    with CENTERS.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=base_cols + ["district", "lat", "lng", "geo_approx"])
        w.writeheader()
        w.writerows(rows)
    for name, level in levels.items():
        if level != "地址":
            print(f"  {name}：{level}")
    print(f"{len(rows)} 間中心；地址查到 {sum(v == '地址' for v in levels.values())} 間，"
          f"geo_approx=1（用行政區中心點）{len(approx)} 間")


if __name__ == "__main__":
    main()
