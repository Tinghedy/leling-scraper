#!/usr/bin/env python3
"""
用 Claude 從課程貼文抽出結構化的課程，給 app.py 的搜尋 dashboard 用。

輸入：output/courses.csv（run_daily.py 產生的課程相關貼文）；有本機課表圖片的貼文，圖片一起送給模型看
輸出：data/courses.json（一門課一筆），快取在 data/cache/（以貼文網址當 key，重跑不重複呼叫）

去識別化：輸出只有下面 FIELDS 的欄位。不存發文者，講師一律空值；模型被要求不要把人名、電話
寫進任何欄位，程式再把看起來像電話、「某某老師」的字樣拿掉。執行時只印數字，不印貼文內容。

需要：環境變數 ANTHROPIC_API_KEY
用法：
  uv run --python 3.12 --with-requirements requirements.txt python extract.py --limit 10
  ... extract.py --since 2026-09-01 --no-images
"""

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import json
import re
import sys
from pathlib import Path

import anthropic

MODEL = "claude-sonnet-5-5"
MAX_TOKENS = 16000
MAX_IMAGES = 6  # 一則貼文最多送幾張圖

BASE = Path(__file__).resolve().parent
POSTS = BASE / "output" / "courses.csv"
CENTERS = BASE / "centers.csv"
OUT = BASE / "data" / "courses.json"
CACHE = BASE / "data" / "cache"

# 和 run_daily.py 的 TOPICS 相同的 7 類
DOMAINS = ["數位科技", "健康運動", "藝術手作", "音樂表演", "語言文化", "生活財務", "社區志工"]
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
LLM_FIELDS = ["course_name", "weekday", "start_time", "end_time", "start_date", "end_date", "venue", "domain"]
FIELDS = ["center", "district", "lat", "lng", "course_name", "weekday", "start_time", "end_time", "time_slot",
          "start_date", "end_date", "venue", "domain", "instructor", "inferred_fields", "source_url",
          "source_type", "post_date"]


def nullable(schema: dict) -> dict:
    return {"anyOf": [schema, {"type": "null"}]}


SCHEMA = {
    "type": "object",
    "properties": {
        "courses": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "course_name": {"type": "string"},
                    "weekday": {"type": "array", "items": {"type": "string", "enum": WEEKDAYS}},
                    "start_time": nullable({"type": "string", "description": "HH:MM，24 小時制"}),
                    "end_time": nullable({"type": "string", "description": "HH:MM，24 小時制"}),
                    "start_date": nullable({"type": "string", "description": "YYYY-MM-DD"}),
                    "end_date": nullable({"type": "string", "description": "YYYY-MM-DD"}),
                    "venue": nullable({"type": "string"}),
                    "domain": nullable({"type": "string", "enum": DOMAINS}),
                    "inferred_fields": {"type": "array", "items": {"type": "string", "enum": LLM_FIELDS}},
                    "source_type": {"type": "string", "enum": ["text", "image"]},
                },
                "required": ["course_name", "weekday", "start_time", "end_time", "start_date", "end_date",
                             "venue", "domain", "inferred_fields", "source_type"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["courses"],
    "additionalProperties": False,
}

SYSTEM = f"""你負責從台南樂齡學習中心的 Facebook 貼文（和附上的課表圖片）抽出「課程公告」裡的課程。

規則：
- 一則貼文可以有 0 到多門課。不是課程公告的貼文（成果分享、上課花絮、活動照片、新聞轉貼、節日問候等）回傳 courses: []。
- 原文沒寫的欄位一律填 null（weekday 填 []），不要用常識補。
- 任何不是原文直接寫出來、而是你推論的值（例如從日期算出星期、從「今天」換算日期、從課名判斷 domain、只寫月日時補年份以外的推論），把欄位名稱列進 inferred_fields。照發文日期補年份不算推論。
- weekday 用「一」到「日」，可以多個。時間用 24 小時制 HH:MM（「下午2點」→ 14:00）。日期用 YYYY-MM-DD。
- 課程有好幾堂時，start_date 是第一堂、end_date 是最後一堂。
- domain 從這 7 類選一個：{"、".join(DOMAINS)}；判斷不出來填 null。domain 一定是推論，要列進 inferred_fields。
- source_type：這門課是從貼文文字讀的填 text，從圖片讀的填 image。
- 去識別化：course_name 和 venue 不要包含任何人名（講師、學員、工作人員）、電話、Email、LINE ID。"""

_PHONE = re.compile(r"(?:\+?886[-\s]?|0)\d{1,3}[-\s]?\d{3,4}[-\s]?\d{3,4}(?:#\d+)?|09\d{2}-?\d{3}-?\d{3}")
_TEACHER = re.compile(r"[一-鿿]{1,4}\s*(老師|講師|教授|校長|醫師|藥師)")


def scrub(v: str | None) -> str | None:
    """模型漏掉的電話、「某某老師」再拿掉一次。"""
    if not v:
        return v
    v = _TEACHER.sub("", _PHONE.sub("", v))
    v = re.sub(r"[（(]\s*[)）]", "", v)
    return re.sub(r"\s{2,}", " ", v).strip(" -－:：、,，") or None


def time_slot(hm: str | None) -> str | None:
    m = re.fullmatch(r"(\d{1,2}):(\d{2})", hm or "")
    if not m:
        return None
    h = int(m[1])
    return "morning" if h < 12 else "afternoon" if h < 17 else "evening"


def cache_key(row: dict) -> str:
    key = row["連結"].strip() or f"{row['中心']}|{row['發文時間']}"
    return hashlib.sha1(key.encode()).hexdigest()


def image_blocks(row: dict) -> list[dict]:
    out = []
    for rel in [p for p in row.get("圖片路徑", "").split("；") if p][:MAX_IMAGES]:
        path = BASE / rel
        if not path.exists():
            continue
        media = "image/png" if path.suffix.lower() == ".png" else "image/jpeg"
        out.append({"type": "image", "source": {"type": "base64", "media_type": media,
                                                 "data": base64.standard_b64encode(path.read_bytes()).decode()}})
    return out


def call_llm(client: anthropic.Anthropic, row: dict, images: list[dict]) -> list[dict]:
    text = (f"中心：{row['中心']}\n發文日期：{row['發文時間'][:10]}\n"
            f"附上的課表圖片：{len(images)} 張\n\n貼文內文：\n{row['內文']}")
    response = client.beta.messages.create(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        # 安全分類器拒答時，由伺服器自動改用其他模型重跑
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
        system=[{"type": "text", "text": SYSTEM, "cache_control": {"type": "ephemeral"}}],
        output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        messages=[{"role": "user", "content": images + [{"type": "text", "text": text}]}],
    )
    if response.stop_reason == "refusal":
        raise RuntimeError("模型拒答")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("輸出太長被截斷")
    body = next(b.text for b in response.content if b.type == "text")
    return json.loads(body)["courses"]


def load_centers() -> dict[str, dict]:
    with CENTERS.open(encoding="utf-8-sig", newline="") as f:
        return {r["name"]: r for r in csv.DictReader(f)}


def to_record(c: dict, row: dict, center: dict) -> dict:
    lat, lng = center.get("lat"), center.get("lng")
    rec = {
        "center": row["中心"], "district": center.get("district") or None,
        "lat": float(lat) if lat else None, "lng": float(lng) if lng else None,
        "course_name": scrub(c["course_name"]), "weekday": [w for w in WEEKDAYS if w in c["weekday"]],
        "start_time": c["start_time"], "end_time": c["end_time"], "time_slot": time_slot(c["start_time"]),
        "start_date": c["start_date"], "end_date": c["end_date"], "venue": scrub(c["venue"]),
        "domain": c["domain"], "instructor": None, "inferred_fields": sorted(set(c["inferred_fields"])),
        "source_url": row["連結"].strip() or None, "source_type": c["source_type"],
        "post_date": row["發文時間"][:10],
    }
    return {k: rec[k] for k in FIELDS}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=0, help="只處理前 N 則（先小量測試）")
    ap.add_argument("--since", default="", help="只處理這天以後發的貼文，例如 2026-09-01")
    ap.add_argument("--images", action=argparse.BooleanOptionalAction, default=True,
                    help="有本機課表圖片的貼文，圖片一起送給模型（--no-images 只送文字）")
    args = ap.parse_args()

    with POSTS.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if r["發文時間"][:10] >= args.since]
    rows.sort(key=lambda r: r["發文時間"], reverse=True)  # 新的先做
    if args.limit:
        rows = rows[:args.limit]
    centers = load_centers()
    CACHE.mkdir(parents=True, exist_ok=True)
    client = anthropic.Anthropic()

    records, failed, called = [], 0, 0
    for i, row in enumerate(rows, 1):
        path = CACHE / f"{cache_key(row)}.json"
        if path.exists():
            courses = json.loads(path.read_text(encoding="utf-8"))["courses"]
        else:
            images = image_blocks(row) if args.images else []
            try:
                courses = call_llm(client, row, images)
            except (anthropic.APIError, RuntimeError, json.JSONDecodeError, StopIteration) as e:
                failed += 1
                print(f"  第 {i} 則失敗：{type(e).__name__}", file=sys.stderr)
                continue
            called += 1
            path.write_text(json.dumps({"model": MODEL, "images": len(images), "courses": courses},
                                       ensure_ascii=False), encoding="utf-8")
        records += [to_record(c, row, centers.get(row["中心"], {})) for c in courses]
        if i % 20 == 0:
            print(f"  {i}/{len(rows)} 則…", file=sys.stderr)

    OUT.write_text(json.dumps(records, ensure_ascii=False, indent=1), encoding="utf-8")
    n_inf = sum(1 for r in records if r["inferred_fields"])
    print(f"處理 {len(rows)} 則貼文（呼叫模型 {called} 次，其餘用快取），抽出 {len(records)} 門課，"
          f"有 inferred 欄位 {n_inf / len(records):.0%}，失敗 {failed} 則" if records else
          f"處理 {len(rows)} 則貼文，沒有抽出課程，失敗 {failed} 則")


if __name__ == "__main__":
    main()
