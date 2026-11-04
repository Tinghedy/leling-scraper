#!/usr/bin/env python3
"""
從 data/courses.json 隨機抽 20 門課，輸出 data/sample_check.csv，給人工核對 weekday 和 time_slot。

欄位：課名、中心、抽出的星期、抽出的開始時間、抽出的時段、原文連結、正確?（空白，人工填）
用固定的亂數種子，重跑會抽到同一批；要換一批用 --seed。

用法：python3 sample.py [--n 20] [--seed 20261105]
"""

from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path

BASE = Path(__file__).resolve().parent
COURSES = BASE / "data" / "courses.json"
OUT = BASE / "data" / "sample_check.csv"
SLOTS = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=20)
    ap.add_argument("--seed", type=int, default=20261105)
    args = ap.parse_args()
    courses = json.loads(COURSES.read_text(encoding="utf-8"))
    picked = random.Random(args.seed).sample(courses, min(args.n, len(courses)))
    # utf-8-sig：用 Excel 直接打開不會亂碼
    with OUT.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["課名", "中心", "抽出的星期", "抽出的開始時間", "抽出的時段", "原文連結", "正確?"])
        for c in picked:
            w.writerow([c["course_name"], c["center"], "、".join(c["weekday"]) or "（沒寫）",
                        c["start_time"] or "（沒寫）", SLOTS.get(c["time_slot"], "（沒寫）"), c["source_url"] or "", ""])
    print(f"抽了 {len(picked)} 門（共 {len(courses)} 門）→ data/sample_check.csv")


if __name__ == "__main__":
    main()
