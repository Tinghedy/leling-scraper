#!/usr/bin/env python3
"""
用 Gemini 幫 data/courses.json 的課分主題：只送課名（不送地點、講師或其他欄位），每次 50 個。

  主題       run_daily.py 的 7 類 TOPICS 選一類，都不像就回「其他」（存成 domain = null）
  寫回       domain 改成 AI 的結果，加 domain_source = "ai"；原本關鍵字比對的結果留在 domain_keyword
  只送教育部  只處理 source_type = "moe_doc" 的課；FB 來的課名不送（免費額度的內容會被 Google 拿去改善產品）
  快取       同樣的課名只問一次，結果存在 data/topic_cache.json，重跑不重複呼叫
  比對       最後印出 AI 和關鍵字主題的一致率（只算原本有關鍵字主題的課），和 5 個不一致的例子

用法：uv run --python 3.12 --with-requirements requirements.txt python classify_topics.py
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path
from typing import Literal

from google.genai import errors
from pydantic import BaseModel

import llm
from extract import DOMAINS

BASE = Path(__file__).resolve().parent
COURSES = BASE / "data" / "courses.json"
CACHE = BASE / "data" / "topic_cache.json"
BATCH = 50
OTHER = "其他"

Label = Literal["數位科技", "健康運動", "藝術手作", "音樂表演", "語言文化", "生活財務", "社區志工", "其他"]


class Item(BaseModel):
    i: int
    domain: Label


class Result(BaseModel):
    items: list[Item]


SYSTEM = f"""你會收到一批台南樂齡學習中心的課名（每行一個，前面是編號）。替每個課名選一個主題：
{"、".join(DOMAINS)}；都不像就填「{OTHER}」。
- 只看課名判斷，不要猜課名沒寫的內容。
- 每個編號都要回一筆，i 填原本的編號。"""


def classify(names: list[str]) -> dict[str, str]:
    text = "\n".join(f"{i}. {n}" for i, n in enumerate(names))
    result = llm.generate(SYSTEM, [{"type": "text", "text": text}], Result)
    got = {it.i: it.domain for it in result.items if 0 <= it.i < len(names)}
    if len(got) != len(names):
        raise llm.SchemaError(f"回了 {len(got)} 筆，應該是 {len(names)} 筆")
    return {names[i]: d for i, d in got.items()}


def main() -> None:
    courses = json.loads(COURSES.read_text(encoding="utf-8"))
    cache: dict[str, str] = json.loads(CACHE.read_text(encoding="utf-8")) if CACHE.exists() else {}
    names = sorted({c["course_name"] for c in courses if c["source_type"] == "moe_doc" and c["course_name"]})
    todo = [n for n in names if n not in cache]
    started, failed = time.time(), 0
    for k in range(0, len(todo), BATCH):
        try:
            cache.update(classify(todo[k:k + BATCH]))
        except ValueError as e:  # genai.Client() 找不到金鑰
            if "API key" in str(e):
                sys.exit("沒有 API 金鑰：請先 export GEMINI_API_KEY=...")
            raise
        except (llm.SchemaError, errors.APIError, RuntimeError) as e:
            failed += 1
            print(f"  第 {k // BATCH + 1} 批失敗：{type(e).__name__}: {e}"[:200], file=sys.stderr)
            continue
        CACHE.write_text(json.dumps(cache, ensure_ascii=False, indent=1), encoding="utf-8")

    for c in courses:
        if c["source_type"] != "moe_doc" or c["course_name"] not in cache:
            continue
        if c.get("domain_source") != "ai":
            c["domain_keyword"] = c["domain"]
        label = cache[c["course_name"]]
        c["domain"], c["domain_source"] = (None if label == OTHER else label), "ai"
    COURSES.write_text(json.dumps(courses, ensure_ascii=False, indent=1), encoding="utf-8")

    done = [c for c in courses if c.get("domain_source") == "ai"]
    print(f"{len(names)} 個不同的課名（{len(done)} 門課），這次呼叫模型 {llm.USAGE['requests']} 次，"
          f"失敗 {failed} 批；模型 {llm.MODEL}，耗時 {time.time() - started:.0f} 秒")
    print(f"「{OTHER}」{sum(c['domain'] is None for c in done)} 門；"
          + "、".join(f"{d} {sum(c['domain'] == d for c in done)}" for d in DOMAINS))
    both = [c for c in done if c.get("domain_keyword")]
    same = [c for c in both if c["domain"] == c["domain_keyword"]]
    if both:
        print(f"和關鍵字主題比對：{len(same)}/{len(both)} 門一致（{len(same) / len(both):.0%}）")
        diff = {c["course_name"]: c for c in both if c["domain"] != c["domain_keyword"]}  # 同課名只列一次
        for c in list(diff.values())[:5]:
            print(f"  {c['course_name']}：關鍵字 {c['domain_keyword']} → AI {c['domain'] or OTHER}")


if __name__ == "__main__":
    main()
