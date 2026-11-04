"""
台南樂齡課程搜尋。只在本機執行，不要部署或公開分享。

  手動篩選（星期、時段、主題、行政區）→ 地圖（一個中心一個圓點，面積 ∝ 符合的課數，
  顏色 = 該中心最多的主題）＋課程清單＋原文連結

資料：data/courses.json（import_courses.py 從 leling-courses 的樂齡課程.xlsx 轉出）、centers.csv（geocode_centers.py 補了座標）
不呼叫任何模型，也不需要 API 金鑰。口語查詢（Gemini 把一句話轉成篩選條件）先拿掉了，
要加回來可以參考 commit f918c89 的 app.py。
啟動：uv run --python 3.12 --with-requirements requirements.txt streamlit run app.py
"""

from __future__ import annotations

import csv
import html
import json
import math
from datetime import datetime
from pathlib import Path

import pydeck as pdk
import streamlit as st

BASE = Path(__file__).resolve().parent
COURSES = BASE / "data" / "courses.json"
CENTERS = BASE / "centers.csv"

# 和 extract.py 相同（不 import extract，因為它會載入 Gemini 的 SDK）
DOMAINS = ["數位科技", "健康運動", "藝術手作", "音樂表演", "語言文化", "生活財務", "社區志工"]
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
SLOTS = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}
FIELD_ZH = {"course_name": "課名", "weekday": "星期", "start_time": "開始時間", "end_time": "結束時間",
            "start_date": "開始日期", "end_date": "結束日期", "venue": "地點"}
# 色盲友善的配色（Okabe–Ito）；沒有符合課程的中心用淡灰色
COLORS = {"數位科技": [0, 114, 178], "健康運動": [0, 158, 115], "藝術手作": [213, 94, 0],
          "音樂表演": [204, 121, 167], "語言文化": [230, 159, 0], "生活財務": [86, 180, 233],
          "社區志工": [90, 90, 90]}
NO_DOMAIN, EMPTY = [130, 130, 130], [205, 205, 205]
SOURCE_ZH = {"moe_doc": "教育部樂齡學習網公告", "fb_post": "FB 貼文"}

# 台南 37 區的相鄰關係：由內政部鄉鎮界線（taiwan-atlas towns-10t）算出，邊界有共用頂點就算相鄰
NEIGHBORS = {
    "七股區": ["佳里區", "安南區", "將軍區", "西港區"],
    "下營區": ["六甲區", "學甲區", "官田區", "新營區", "柳營區", "鹽水區", "麻豆區"],
    "中西區": ["北區", "南區", "安南區", "安平區", "東區"],
    "仁德區": ["南區", "東區", "歸仁區", "永康區"],
    "佳里區": ["七股區", "學甲區", "將軍區", "西港區", "麻豆區"],
    "六甲區": ["下營區", "大內區", "官田區", "東山區", "柳營區", "楠西區"],
    "北區": ["中西區", "安南區", "東區", "永康區"],
    "北門區": ["學甲區", "將軍區"],
    "南化區": ["左鎮區", "楠西區", "玉井區"],
    "南區": ["中西區", "仁德區", "安平區", "東區"],
    "善化區": ["大內區", "安定區", "官田區", "山上區", "新市區", "麻豆區"],
    "大內區": ["六甲區", "善化區", "官田區", "山上區", "楠西區", "玉井區"],
    "學甲區": ["下營區", "佳里區", "北門區", "將軍區", "鹽水區", "麻豆區"],
    "安南區": ["七股區", "中西區", "北區", "安定區", "安平區", "新市區", "永康區", "西港區"],
    "安定區": ["善化區", "安南區", "新市區", "西港區", "麻豆區"],
    "安平區": ["中西區", "南區", "安南區"],
    "官田區": ["下營區", "六甲區", "善化區", "大內區", "麻豆區"],
    "將軍區": ["七股區", "佳里區", "北門區", "學甲區"],
    "山上區": ["善化區", "大內區", "左鎮區", "新化區", "新市區", "玉井區"],
    "左鎮區": ["南化區", "山上區", "新化區", "玉井區", "龍崎區"],
    "後壁區": ["新營區", "東山區", "白河區", "鹽水區"],
    "新化區": ["山上區", "左鎮區", "新市區", "歸仁區", "永康區", "關廟區", "龍崎區"],
    "新市區": ["善化區", "安南區", "安定區", "山上區", "新化區", "永康區"],
    "新營區": ["下營區", "後壁區", "東山區", "柳營區", "鹽水區"],
    "東區": ["中西區", "仁德區", "北區", "南區", "永康區"],
    "東山區": ["六甲區", "後壁區", "新營區", "柳營區", "楠西區", "白河區"],
    "柳營區": ["下營區", "六甲區", "新營區", "東山區"],
    "楠西區": ["六甲區", "南化區", "大內區", "東山區", "玉井區"],
    "歸仁區": ["仁德區", "新化區", "永康區", "關廟區"],
    "永康區": ["仁德區", "北區", "安南區", "新化區", "新市區", "東區", "歸仁區"],
    "玉井區": ["南化區", "大內區", "山上區", "左鎮區", "楠西區"],
    "白河區": ["後壁區", "東山區"],
    "西港區": ["七股區", "佳里區", "安南區", "安定區", "麻豆區"],
    "關廟區": ["新化區", "歸仁區", "龍崎區"],
    "鹽水區": ["下營區", "學甲區", "後壁區", "新營區"],
    "麻豆區": ["下營區", "佳里區", "善化區", "學甲區", "安定區", "官田區", "西港區"],
    "龍崎區": ["左鎮區", "新化區", "關廟區"],
}
DISTRICTS = sorted(NEIGHBORS)

st.set_page_config(page_title="台南樂齡課程搜尋", layout="wide")
# 使用者是長者和家屬：字放大、對比加深
st.markdown("""<style>
html, body, [class*="css"], .stMarkdown, p, li, label, input, button, td, th { font-size: 20px !important; }
h1 { font-size: 34px !important; } h2, h3 { font-size: 26px !important; }
body, p, td, th, label { color: #111 !important; }
.courses { border-collapse: collapse; width: 100%; }
.courses th, .courses td { border-bottom: 1px solid #888; padding: 10px 8px; text-align: left; vertical-align: top; }
.courses th { background: #e8e8e8; }
.warn { color: #8a3b00; font-weight: 600; }
a { color: #0645ad !important; text-decoration: underline; }
</style>""", unsafe_allow_html=True)


@st.cache_data
def load_courses(mtime: float) -> list[dict]:
    return json.loads(COURSES.read_text(encoding="utf-8"))


@st.cache_data
def load_centers() -> list[dict]:
    with CENTERS.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("lat") and r.get("lng")]


def no_time(c: dict) -> bool:
    return not c["weekday"] or not c["start_time"]


def matches(c: dict, wd: list, slot: list, dom: list, dist: list, keep_no_time: bool) -> bool:
    """keep_no_time：原文沒寫星期（或時間）的課，不被星期（或時段）條件刷掉。"""
    if no_time(c) and not keep_no_time:
        return False
    return ((not wd or set(c["weekday"]) & set(wd) or (keep_no_time and not c["weekday"]))
            and (not slot or c["time_slot"] in slot or (keep_no_time and not c["start_time"]))
            and (not dom or c["domain"] in dom) and (not dist or c["district"] in dist))


def when(c: dict) -> str:
    days = ("週" + "、".join(c["weekday"])) if c["weekday"] else "星期沒寫"
    t = f"{c['start_time']}–{c['end_time'] or '?'}" if c["start_time"] else "時間沒寫"
    return f"{days} {t}"


for k, v in (("f_weekday", []), ("f_slot", []), ("f_domain", []), ("f_district", []), ("map_key", 0)):
    st.session_state.setdefault(k, v)

st.title("台南樂齡課程搜尋")
if not COURSES.exists():
    st.warning("還沒有課程資料。請先執行 import_courses.py 產生 data/courses.json。")
    st.stop()
courses = load_courses(COURSES.stat().st_mtime)
centers = load_centers()

st.markdown("**篩選條件**")
c1, c2, c3, c4 = st.columns(4)
wd = c1.multiselect("星期", WEEKDAYS, key="f_weekday", format_func=lambda w: "週" + w)
slot = c2.multiselect("時段", list(SLOTS), key="f_slot", format_func=SLOTS.get)
dom = c3.multiselect("主題", DOMAINS, key="f_domain")
dist = c4.multiselect("行政區", DISTRICTS, key="f_district")
keep_no_time = st.checkbox("包含沒寫星期／時間的課", value=True)

hits = [c for c in courses if matches(c, wd, slot, dom, dist, keep_no_time)]

# ── 地圖 ──
points = []
for ctr in centers:
    mine = [c for c in hits if c["center"] == ctr["name"]]
    top = max(DOMAINS, key=lambda d: sum(c["domain"] == d for c in mine)) if mine else None
    color = (COLORS[top] if top and any(c["domain"] == top for c in mine) else NO_DOMAIN) if mine else EMPTY
    points.append({"name": ctr["name"], "count": len(mine), "lat": float(ctr["lat"]), "lng": float(ctr["lng"]),
                   "radius": 350 * math.sqrt(len(mine)) if mine else 250,  # 面積和課數成正比
                   "color": color + [230 if mine else 140],
                   "top": top or ("沒有符合的課" if not mine else "主題沒判斷")})
layer = pdk.Layer("ScatterplotLayer", data=points, id="centers", get_position="[lng, lat]",
                  get_radius="radius", radius_units="meters", get_fill_color="color",
                  get_line_color=[40, 40, 40], line_width_min_pixels=1, stroked=True, pickable=True)
deck = pdk.Deck(layers=[layer], initial_view_state=pdk.ViewState(latitude=23.12, longitude=120.32, zoom=9),
                tooltip={"text": "{name}\n符合的課：{count} 門\n最多的主題：{top}"})
event = st.pydeck_chart(deck, on_select="rerun", selection_mode="single-object",
                        key=f"map{st.session_state.map_key}", height=520)
st.caption("顏色＝該中心最多的主題：" + "　".join(f"{d}" for d in DOMAINS) + "　灰色＝沒有符合的課。點圓點只看那間中心的課。")
picked = None
sel = (event.selection or {}).get("objects", {}).get("centers") if event else None
if sel:
    picked = sel[0]["name"]

# ── 課程清單 ──
shown = [c for c in hits if not picked or c["center"] == picked]
head = f"符合條件的課：{len(shown)} 門" + (f"（只看 {picked}）" if picked else "")
st.subheader(head)
if picked and st.button("看全部中心的課"):
    st.session_state.map_key += 1
    st.rerun()
rows = []
for c in sorted(shown, key=lambda c: (c["start_date"] or "9999", c["center"])):
    inf = "、".join(FIELD_ZH.get(f, f) for f in c["inferred_fields"])
    link = f'<a href="{html.escape(c["source_url"])}" target="_blank" rel="noopener">原文</a>' if c["source_url"] else ""
    rows.append("<tr>" + "".join(f"<td>{x}</td>" for x in (
        html.escape(c["course_name"] or "")
        + ('<br><span class="warn">⚠ 原文未註明時間</span>' if no_time(c) else "")
        + (f'<br><span class="warn">⚠ 推論（原文沒明寫）：{html.escape(inf)}</span>' if inf else ""),
        html.escape(c["center"].replace("樂齡學習中心", "")), html.escape(when(c)), html.escape(c["venue"] or "沒寫"),
        html.escape(c["domain"] or "—"), link)) + "</tr>")
st.markdown('<table class="courses"><tr><th>課名</th><th>中心</th><th>星期與時間</th><th>地點</th><th>主題</th>'
            '<th>原文</th></tr>' + "".join(rows) + "</table>" if rows else "沒有符合的課，請放寬條件。",
            unsafe_allow_html=True)

# ── 側邊欄：資料涵蓋範圍 ──
st.sidebar.header("資料涵蓋範圍")
by_source: dict[str, int] = {}
for c in courses:
    by_source[c["source_type"]] = by_source.get(c["source_type"], 0) + 1
st.sidebar.markdown("**資料來源**\n" + "".join(f"- {SOURCE_ZH.get(k, k)}：**{n}** 門\n" for k, n in by_source.items()))
st.sidebar.markdown(f"- 中心：**{len({c['center'] for c in courses})}** / {len(centers)} 間\n- 課程：**{len(courses)}** 門\n"
                    f"- 沒寫星期或時間：{sum(no_time(c) for c in courses)} 門\n"
                    f"- 資料更新：**{datetime.fromtimestamp(COURSES.stat().st_mtime):%Y-%m-%d}**\n"
                    f"- 貼文日期：{min(c['post_date'] for c in courses)} ～ {max(c['post_date'] for c in courses)}")
st.sidebar.caption("⚠ 表示原文沒寫時間，或這個欄位是推論的（AI 讀圖或照月份展開日期），請點「原文」確認。")
