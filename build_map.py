#!/usr/bin/env python3
"""
把 taiwaneseOMG 專案的臺南市行政區地圖（Figma 設計稿匯出的 SVG 路徑）轉成 tainan_map.json，給 app.py 畫地圖。

來源：~/Downloads/taiwaneseOMG-main/src/placenameCulturePage/tainanMapData.js
輸出：tainan_map.json  {"width", "height", "districts": [{name, bbox, label, mainAreas, mainLines, areas, lines}]}
  座標系統是 viewBox 0 0 793 718；label 是區名標籤的錨點。設計稿更新後重跑一次。

用法：python3 build_map.py [tainanMapData.js 的路徑]
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

SOURCE = Path.home() / "Downloads/taiwaneseOMG-main/src/placenameCulturePage/tainanMapData.js"
OUT = Path(__file__).resolve().parent / "tainan_map.json"


def main(path: Path) -> None:
    js = path.read_text(encoding="utf-8")
    box = re.search(r"MAP_VIEWBOX\s*=\s*\{\s*width:\s*([\d.]+),\s*height:\s*([\d.]+)", js)
    body = re.search(r"TAINAN_DISTRICTS\s*=\s*(\[.*?\n\]);", js, re.S).group(1)
    body = re.sub(r"^(\s*)(\w+):", r'\1"\2":', body, flags=re.M)  # 物件的 key 加引號
    body = re.sub(r",(\s*[\]}])", r"\1", body)  # 拿掉結尾逗號
    districts = json.loads(body)
    OUT.write_text(json.dumps({"width": float(box[1]), "height": float(box[2]), "districts": districts},
                              ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"{len(districts)} 區 → {OUT.name}（{OUT.stat().st_size // 1024} KB）")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else SOURCE)
