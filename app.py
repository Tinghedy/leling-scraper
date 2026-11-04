"""
台南樂齡課程搜尋（T1：用一句話找課）。只在本機執行，不要部署或公開分享。

  輸入一句話（例如「週一早上、運動、東區附近」）→ Claude 轉成篩選條件 → 可以手動修正
  → 地圖（一個中心一個圓點，面積 ∝ 符合的課數，顏色 = 該中心最多的主題）＋課程清單

資料：data/courses.json（extract.py 產生）、centers.csv（geocode_centers.py 補了座標）
需要：環境變數 ANTHROPIC_API_KEY（只有「轉成篩選條件」會用到，手動篩選不需要）
啟動：uv run --python 3.12 --with-requirements requirements.txt streamlit run app.py
"""

from __future__ import annotations

import csv
import html
import json
import math
from datetime import datetime
from pathlib import Path

import anthropic
import pydeck as pdk
import streamlit as st

from extract import DOMAINS, MODEL, WEEKDAYS

BASE = Path(__file__).resolve().parent
COURSES = BASE / "data" / "courses.json"
CENTERS = BASE / "centers.csv"

SLOTS = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}
FIELD_ZH = {"course_name": "課名", "weekday": "星期", "start_time": "開始時間", "end_time": "結束時間",
            "start_date": "開始日期", "end_date": "結束日期", "venue": "地點"}
# 色盲友善的配色（Okabe–Ito）；沒有符合課程的中心用淡灰色
COLORS = {"數位科技": [0, 114, 178], "健康運動": [0, 158, 115], "藝術手作": [213, 94, 0],
          "音樂表演": [204, 121, 167], "語言文化": [230, 159, 0], "生活財務": [86, 180, 233],
          "社區志工": [90, 90, 90]}
NO_DOMAIN, EMPTY = [130, 130, 130], [205, 205, 205]

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

QUERY_SCHEMA = {
    "type": "object",
    "properties": {
        "weekday": {"type": "array", "items": {"type": "string", "enum": WEEKDAYS}},
        "time_slot": {"type": "array", "items": {"type": "string", "enum": list(SLOTS)}},
        "domain": {"type": "array", "items": {"type": "string", "enum": DOMAINS}},
        "district": {"type": "array", "items": {"type": "string", "enum": DISTRICTS}},
        "near": {"type": "boolean"},
    },
    "required": ["weekday", "time_slot", "domain", "district", "near"],
    "additionalProperties": False,
}
QUERY_SYSTEM = f"""把使用者找樂齡課程的一句話，轉成篩選條件。沒提到的條件填空陣列。
- weekday：「一」到「日」。「平日」＝一到五，「週末」＝六、日。
- time_slot：早上、上午 → morning；中午過後、下午 → afternoon；晚上 → evening。
- domain：從 {"、".join(DOMAINS)} 選，可以多個（例如「運動」→ 健康運動，「手機」→ 數位科技，「唱歌」→ 音樂表演）。
- district：台南市的行政區，要寫成「東區」「永康區」這種完整名稱。
- near：使用者說「附近」「周邊」「旁邊」時填 true（程式會再加上相鄰的行政區），否則 false。"""

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


def parse_query(q: str) -> dict:
    client = anthropic.Anthropic()
    response = client.beta.messages.create(
        model=MODEL, max_tokens=4000,
        betas=["server-side-fallback-2026-07-01"], fallbacks="default",
        system=QUERY_SYSTEM,
        output_config={"format": {"type": "json_schema", "schema": QUERY_SCHEMA}},
        messages=[{"role": "user", "content": q}],
    )
    if response.stop_reason in ("refusal", "max_tokens"):
        raise RuntimeError("模型沒有給出篩選條件")
    return json.loads(next(b.text for b in response.content if b.type == "text"))


def on_query() -> None:
    """按下「找課」：把 AI 轉出的條件填進下面的選單（選單在這之後才畫，所以可以直接改 session_state）。"""
    q = st.session_state.q.strip()
    if not q:
        return
    try:
        f = parse_query(q)
    except TypeError as e:  # SDK 找不到金鑰時丟 TypeError
        if "authentication" not in str(e):
            raise
        st.session_state.query_error = "沒有設定 ANTHROPIC_API_KEY，AI 轉換不能用，請直接用下面的選單篩選。"
        return
    except (anthropic.APIError, RuntimeError, json.JSONDecodeError, StopIteration) as e:
        st.session_state.query_error = f"AI 沒辦法轉換這句話（{type(e).__name__}），請直接用下面的選單篩選。"
        return
    districts = set(f["district"])
    if f["near"]:
        districts |= {n for d in f["district"] for n in NEIGHBORS.get(d, [])}
    st.session_state.update({
        "f_weekday": f["weekday"], "f_slot": f["time_slot"], "f_domain": f["domain"],
        "f_district": sorted(districts), "query_error": "",
        "query_note": "已加上相鄰的行政區：" + "、".join(sorted(districts - set(f["district"])))
        if f["near"] and districts - set(f["district"]) else "",
    })
    st.session_state.map_key += 1  # 換條件時清掉地圖上點選的中心


def matches(c: dict, wd: list, slot: list, dom: list, dist: list) -> bool:
    return ((not wd or set(c["weekday"] or []) & set(wd)) and (not slot or c["time_slot"] in slot)
            and (not dom or c["domain"] in dom) and (not dist or c["district"] in dist))


def when(c: dict) -> str:
    days = ("週" + "、".join(c["weekday"])) if c["weekday"] else "星期沒寫"
    t = f"{c['start_time']}–{c['end_time'] or '?'}" if c["start_time"] else "時間沒寫"
    return f"{days} {t}"


for k, v in (("f_weekday", []), ("f_slot", []), ("f_domain", []), ("f_district", []), ("map_key", 0),
             ("query_error", ""), ("query_note", "")):
    st.session_state.setdefault(k, v)

st.title("台南樂齡課程搜尋")
if not COURSES.exists():
    st.warning("還沒有課程資料。請先執行 extract.py 產生 data/courses.json。")
    st.stop()
courses = load_courses(COURSES.stat().st_mtime)
centers = load_centers()

with st.form("query"):
    st.text_input("想找什麼課？用一句話說就好", key="q", placeholder="例如：週一早上、運動、東區附近")
    st.form_submit_button("找課", on_click=on_query)
if st.session_state.query_error:
    st.error(st.session_state.query_error)

st.markdown("**篩選條件**（AI 判斷的結果，可以直接改）")
c1, c2, c3, c4 = st.columns(4)
wd = c1.multiselect("星期", WEEKDAYS, key="f_weekday", format_func=lambda w: "週" + w)
slot = c2.multiselect("時段", list(SLOTS), key="f_slot", format_func=SLOTS.get)
dom = c3.multiselect("主題", DOMAINS, key="f_domain")
dist = c4.multiselect("行政區", DISTRICTS, key="f_district")
if st.session_state.query_note:
    st.caption(st.session_state.query_note)

hits = [c for c in courses if matches(c, wd, slot, dom, dist)]

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
        html.escape(c["course_name"] or "") + (f'<br><span class="warn">⚠ AI 推論：{html.escape(inf)}</span>' if inf else ""),
        html.escape(c["center"].replace("樂齡學習中心", "")), html.escape(when(c)), html.escape(c["venue"] or "沒寫"),
        html.escape(c["domain"] or "—"), link)) + "</tr>")
st.markdown('<table class="courses"><tr><th>課名</th><th>中心</th><th>星期與時間</th><th>地點</th><th>主題</th>'
            '<th>原文</th></tr>' + "".join(rows) + "</table>" if rows else "沒有符合的課，請放寬條件。",
            unsafe_allow_html=True)

# ── 側邊欄：資料涵蓋範圍 ──
st.sidebar.header("資料涵蓋範圍")
st.sidebar.markdown(f"- 中心：**{len({c['center'] for c in courses})}** 間\n- 課程：**{len(courses)}** 門\n"
                    f"- 資料更新：**{datetime.fromtimestamp(COURSES.stat().st_mtime):%Y-%m-%d}**\n"
                    f"- 貼文日期：{min(c['post_date'] for c in courses)} ～ {max(c['post_date'] for c in courses)}")
st.sidebar.caption("⚠ 表示這個欄位原文沒有明寫，是 AI 推論的，請點「原文」確認。")
