"""
台南樂齡課程搜尋。只在本機執行，不要部署或公開分享。

  手動篩選（日期、星期、時段、主題）＋「我住在」→ 行政區地圖（住的區深色、相鄰區淺色、其他灰色，
  每區標出符合條件的課數；點一區只看那區的課，再點一次取消）＋課程清單＋原文連結

資料：data/courses.json（import_courses.py 從 leling-courses 的樂齡課程.xlsx 轉出）、centers.csv（中心所在的行政區）、
      tainan_map.json（build_map.py 從 taiwaneseOMG 的 Figma 地圖設計稿轉出）
不呼叫任何模型，也不需要 API 金鑰。口語查詢（Gemini 把一句話轉成篩選條件）先拿掉了，
要加回來可以參考 commit f918c89 的 app.py。
啟動：uv run --python 3.12 --with-requirements requirements.txt streamlit run app.py
"""

from __future__ import annotations

import csv
import html
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import streamlit as st

try:
    from zoneinfo import ZoneInfo
    TPE = ZoneInfo("Asia/Taipei")
except Exception:  # 沒有時區資料庫時：台灣沒有日光節約，固定 UTC+8 等價
    TPE = timezone(timedelta(hours=8))

BASE = Path(__file__).resolve().parent
COURSES = BASE / "data" / "courses.json"
CENTERS = BASE / "centers.csv"
MAP = BASE / "tainan_map.json"

# 和 extract.py 相同（不 import extract，因為它會載入 Gemini 的 SDK）
DOMAINS = ["數位科技", "健康運動", "藝術手作", "音樂表演", "語言文化", "生活財務", "社區志工"]
WEEKDAYS = ["一", "二", "三", "四", "五", "六", "日"]
SLOTS = {"morning": "上午", "afternoon": "下午", "evening": "晚上"}
FIELD_ZH = {"course_name": "課名", "weekday": "星期", "start_time": "開始時間", "end_time": "結束時間",
            "start_date": "開始日期", "end_date": "結束日期", "venue": "地點"}
# 地圖：住的區深藍、相鄰區淺藍、其他區灰色（同一色系的深淺，不靠紅綠區分）；選到的區加橘色粗框
MAP_CSS = """
.map { max-width: 780px; margin: 0 auto; }
svg { width: 100%; height: auto; display: block; }
.d { cursor: pointer; outline: none; }
.a { stroke: #fff; stroke-width: 1.2; }
.l { fill: none; stroke: #fff; stroke-width: 1.2; }
.home .a { fill: #08519c; } .near .a { fill: #9ecae1; } .other .a { fill: #dcdcdc; }
.zero .a { fill: #f6f6f6; } .home.zero .a { stroke: #08519c; stroke-width: 3; }
.t.zero { fill: #777; }
.d:hover .a, .d:focus-visible .a { filter: brightness(0.85); }
.picked .a { stroke: #e66100; stroke-width: 4; }
.t { font: 700 11px "PingFang TC", "Noto Sans TC", "Microsoft JhengHei", sans-serif; text-anchor: middle;
     pointer-events: none; fill: #111; paint-order: stroke; stroke: #fff; stroke-width: 3px; }
.t.home { fill: #fff; stroke: #08519c; }
"""
# 點一區（或用鍵盤 Enter）就把區名傳回 Python
MAP_JS = """
export default function(component) {
    const { data, setTriggerValue, parentElement } = component;
    let box = parentElement.querySelector(".map");
    if (!box) {
        box = document.createElement("div");
        box.className = "map";
        parentElement.appendChild(box);
    }
    box.innerHTML = data;
    box.querySelectorAll("[data-name]").forEach((g) => {
        g.onclick = () => setTriggerValue("clicked", g.dataset.name);
        g.onkeydown = (e) => {
            if (e.key === "Enter" || e.key === " ") {
                e.preventDefault();
                setTriggerValue("clicked", g.dataset.name);
            }
        };
    });
}
"""
district_map = st.components.v2.component("tainan_district_map", css=MAP_CSS, js=MAP_JS)
SHOW_INFERRED = False  # 清單裡要不要標「⚠ 推論（原文沒明寫）」；暫時隱藏
STATUS_ZH = {"upcoming": "即將開課", "ongoing": "進行中", "ended": "已結束", "unknown": "日期沒寫"}
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

# ── 地點正規化：只用在顯示和分組，不改 data/courses.json ──
_DIST = "|".join(DISTRICTS)
_SUFFIX = ("社區照顧關懷據點", "社區關懷據點", "關懷據點", "社區活動中心", "社區發展協會", "里活動中心", "活動中心", "社區", "里")


def norm_venue(v: str | None) -> str:
    """「嘉田」「嘉田社區」→ 嘉田；「本部一樓」「本部1樓」→ 本部1樓；去掉括號、門牌地址、空白。
    只拿掉明確的地址（有「X區…號」或「臺南市…」），不同地點（如「竹門國小」和「竹門社區」）不會變成一樣。"""
    if not v:
        return ""
    s = re.sub(r"[(（][^)）]*[)）]", "", v)                       # 括號裡的補充說明或地址
    s = re.sub(r"[-－:：]\s*[臺台]南市.*$", "", s)                 # 「喜樹國小-臺南市南區…」
    s = re.sub(rf"\s+(?:[臺台]南市|{_DIST}).*$", "", s)            # 「文康中心 臺南市鹽水區…」
    s = re.sub(rf"(?:[臺台]南市)?(?:{_DIST})\S*?號.*$", "", s)     # 「紫竹據點龍崎區龍船里5鄰11號」
    s = re.sub(r"[\s\-－:：・]", "", s)
    s = re.sub(r"([一二三四五])樓", lambda m: f"{'一二三四五'.index(m.group(1)) + 1}樓", s)
    for suf in _SUFFIX:
        if s.endswith(suf) and len(s) > len(suf) + 1:
            s = s[: -len(suf)]
            break
    return s or v.strip()


def merge_courses(rows: list[dict]) -> list[dict]:
    """課名、中心、正規化地點、星期、時間都相同的列合併：上課日取聯集、原文連結全部保留。
    顯示的地點用該組裡最長（資訊最多）的原始寫法。"""
    groups: dict[tuple, list[dict]] = {}
    for c in rows:
        k = (c["course_name"], c["center"], norm_venue(c["venue"]), tuple(c["weekday"]), c["start_time"], c["end_time"])
        groups.setdefault(k, []).append(c)
    out = []
    for g in groups.values():
        m = dict(g[0])
        m["dates"] = sorted({d for c in g for d in c.get("dates") or []})
        m["start_date"], m["end_date"] = (m["dates"][0], m["dates"][-1]) if m["dates"] else (None, None)
        m["venue"] = max((c["venue"] for c in g if c["venue"]), key=len, default=None)
        m["domain"] = next((c["domain"] for c in g if c["domain"]), None)
        m["time_slot"] = next((c["time_slot"] for c in g if c["time_slot"]), None)
        m["source_urls"] = list(dict.fromkeys(c["source_url"] for c in g if c["source_url"]))
        m["merged_rows"] = len(g)
        out.append(m)
    return out

st.set_page_config(page_title="台南樂齡課程搜尋", layout="wide")
# 使用者是長者和家屬：字放大、對比加深
st.markdown("""<style>
html, body, [class*="css"], .stMarkdown, p, li, label, input, button, td, th { font-size: 20px !important; }
h1 { font-size: 34px !important; } h2, h3 { font-size: 26px !important; }
body, p, td, th, label { color: #111 !important; }
button[kind^="primary"], button[kind^="primary"] p { color: #fff !important; }
.courses { border-collapse: collapse; width: 100%; }
.courses th, .courses td { border-bottom: 1px solid #888; padding: 10px 8px; text-align: left; vertical-align: top; }
.courses th { background: #e8e8e8; }
.warn { color: #8a3b00; font-weight: 600; }
a { color: #0645ad !important; text-decoration: underline; }
</style>""", unsafe_allow_html=True)


@st.cache_data
def load_courses(mtime: float) -> list[dict]:
    return merge_courses(json.loads(COURSES.read_text(encoding="utf-8")))


@st.cache_data
def load_map() -> dict:
    return json.loads(MAP.read_text(encoding="utf-8"))


def map_svg(home: str, near: set[str], counts: dict[str, int], picked: str | None) -> str:
    """區名都來自 tainan_map.json（固定的 37 區），不含使用者輸入。"""
    m = load_map()
    groups, labels = [], []
    # 選到的區最後畫，粗框才不會被鄰區蓋住
    for d in sorted(m["districts"], key=lambda d: d["name"] == picked):
        name, n = d["name"], counts.get(d["name"], 0)
        tier = "home" if name == home else "near" if name in near else "other"
        if n == 0:  # 符合 0 門：不論遠近都用最淡的底色（住的區留深藍框）
            tier += " zero"
        paths = "".join(f'<path class="a" d="{p}"/>' for p in d["areas"]) + \
            "".join(f'<path class="l" d="{p}"/>' for p in d["lines"])
        groups.append(f'<g class="d {tier}{" picked" if name == picked else ""}" data-name="{name}" tabindex="0" '
                      f'role="button" aria-label="{name}，{n} 門課"><title>{name}：{n} 門</title>{paths}</g>')
        x, y = d["label"]
        labels.append(f'<text class="t {tier.replace("home zero", "zero")}" x="{x}" y="{y}"><tspan x="{x}" dy="-0.15em">{name}</tspan>'
                      f'<tspan x="{x}" dy="1.15em">{n}</tspan></text>')
    return (f'<svg viewBox="0 0 {m["width"]} {m["height"]}" aria-label="臺南市行政區地圖">'
            + "".join(groups) + "".join(labels) + "</svg>")


@st.cache_data
def load_centers() -> list[dict]:
    with CENTERS.open(encoding="utf-8-sig", newline="") as f:
        return [r for r in csv.DictReader(f) if r.get("lat") and r.get("lng")]


def no_time(c: dict) -> bool:
    return not c.get("dates") or not c["weekday"] or not c["start_time"]


def missing_text(c: dict) -> str | None:
    """依實際缺的欄位寫警示；有日期就一定有星期，所以只看日期和開始時間。"""
    no_date, no_clock = not c.get("dates"), not c["start_time"]
    if no_date and no_clock:
        return "日期和時間都沒寫"
    if no_date:
        return "上課日期沒寫"
    if no_clock:
        return "上課時間沒寫"
    return None


def phone_html(center: str, phones: dict[str, str]) -> str:
    """centers.csv 有 phone 欄才顯示可撥號的連結；目前沒有這個欄位，只寫「請打電話向中心確認」。"""
    tel = phones.get(center)
    if not tel:
        return "請打電話向中心確認"
    digits = "".join(ch for ch in tel if ch.isdigit() or ch == "+")
    return f'請打電話向中心確認：<a href="tel:{html.escape(digits)}">{html.escape(tel)}</a>'


def matches(c: dict, days: list, wd: list, slot: list, dom: list, keep_no_time: bool) -> bool:
    """keep_no_time：原文沒寫日期、星期（或時間）的課，不被日期、星期（或時段）條件刷掉。"""
    if no_time(c) and not keep_no_time:
        return False
    return ((not days or set(c.get("dates") or []) & set(days) or (keep_no_time and not c.get("dates")))
            and (not wd or set(c["weekday"]) & set(wd) or (keep_no_time and not c["weekday"]))
            and (not slot or c["time_slot"] in slot or (keep_no_time and not c["start_time"]))
            and (not dom or c["domain"] in dom))


def today_tpe() -> str:
    """執行當下的台北日期，YYYY-MM-DD（和 dates 同格式，可以直接比字串）"""
    return datetime.now(TPE).date().isoformat()


def status(c: dict, today: str) -> str:
    """dates 已排序且都有年份。最後一個上課日過了才算已結束；沒有日期的視為未結束（unknown）。"""
    ds = c.get("dates") or []
    if not ds:
        return "unknown"
    if ds[-1] < today:
        return "ended"
    return "upcoming" if ds[0] > today else "ongoing"


def next_date(c: dict, today: str) -> str | None:
    return next((d for d in c.get("dates") or [] if d >= today), None)


def sort_key(c: dict, today: str) -> tuple:
    """未結束的照下一次上課日由近到遠（沒日期的排後面）；已結束的放最後，最近結束的在前。"""
    if status(c, today) == "ended":
        return (1, -date.fromisoformat(c["dates"][-1]).toordinal(), c["center"])
    nd = next_date(c, today)
    return (0, date.fromisoformat(nd).toordinal() if nd else 10**9, c["center"])


def status_text(c: dict, today: str) -> str:
    return "今天上課" if next_date(c, today) == today else STATUS_ZH[status(c, today)]


def md(d: str) -> str:
    """2026-10-08 → 10/8（三）"""
    y, m, dd = (int(x) for x in d.split("-"))
    return f"{m}/{dd}（{WEEKDAYS[date(y, m, dd).weekday()]}）"


def short(d: str) -> str:
    """2026-10-08 → 10/8"""
    return md(d).split("（")[0]


def date_text(c: dict, today: str) -> str:
    """單堂：10/8；多堂：10/1～12/3，共 8 堂，下一堂 11/12"""
    ds = c.get("dates") or []
    if not ds:
        return "日期沒寫"
    if len(ds) == 1:
        return short(ds[0])
    nd = next_date(c, today)
    return (f"{short(ds[0])}～{short(ds[-1])}，共 {len(ds)} 堂"
            + (f"，下一堂 {short(nd)}" if nd else ""))


def when(c: dict) -> str:
    days = ("週" + "、".join(c["weekday"])) if c["weekday"] else "星期沒寫"
    t = f"{c['start_time']}–{c['end_time'] or '?'}" if c["start_time"] else "時間沒寫"
    return f"{days} {t}"


for k, v in (("f_date", []), ("f_weekday", []), ("f_slot", []), ("f_domain", []), ("home", "東區"), ("f_keep_no_time", True), ("f_show_ended", False), ("picked", None), ("picked_near", False)):
    st.session_state.setdefault(k, v)

st.title("台南樂齡課程搜尋")
if not COURSES.exists():
    st.warning("還沒有課程資料。請先執行 import_courses.py 產生 data/courses.json。")
    st.stop()
courses = load_courses(COURSES.stat().st_mtime)
centers = load_centers()
PHONES = {r["name"]: r["phone"].strip() for r in centers if (r.get("phone") or "").strip()}

st.markdown("**篩選條件**")
all_dates = sorted({d for c in courses for d in c.get("dates") or []})
# 選項一變就重跑：地圖數字和列表都用同一份 hits
c0, c1, c2, c3, c4 = st.columns(5)
c0.multiselect("日期", all_dates, key="f_date", format_func=md, placeholder="例如 10/8")
c1.multiselect("星期", WEEKDAYS, key="f_weekday", format_func=lambda w: "週" + w)
c2.multiselect("時段", list(SLOTS), key="f_slot", format_func=SLOTS.get)
c3.multiselect("主題", DOMAINS, key="f_domain")
home = c4.selectbox("我住在", DISTRICTS, key="home")
st.checkbox("包含沒寫日期／星期／時間的課", key="f_keep_no_time")
st.toggle("顯示已結束的課", key="f_show_ended")

today = today_tpe()
FILTER_KEYS = ("f_date", "f_weekday", "f_slot", "f_domain", "f_keep_no_time", "f_show_ended")


def filtered(p: dict) -> list[dict]:
    """p：FILTER_KEYS 對應的值。地圖、列表、放寬建議都經過這裡，條件才會一致。"""
    return [c for c in courses
            if matches(c, p["f_date"], p["f_weekday"], p["f_slot"], p["f_domain"], p["f_keep_no_time"])
            and (p["f_show_ended"] or status(c, today) != "ended")]


def scope(picked: str | None, with_near: bool) -> set[str] | None:
    """點選的區（加上鄰近區）；None＝全台南"""
    if not picked:
        return None
    return {picked} | (set(NEIGHBORS.get(picked, [])) if with_near else set())


params = {k: st.session_state[k] for k in FILTER_KEYS}
hits = filtered(params)

# ── 地圖：行政區分層設色 ──
near = set(NEIGHBORS.get(home, []))
picked = st.session_state.picked
counts: dict[str, int] = {}
for c in hits:
    counts[c["district"]] = counts.get(c["district"], 0) + 1
result = district_map(data=map_svg(home, near, counts, picked), key="district_map", on_clicked_change=lambda: None)
if result.clicked:  # 點同一區＝取消，點別區＝換過去
    st.session_state.picked = None if result.clicked == picked else result.clicked
    st.session_state.picked_near = False
    st.rerun()
st.caption(f"深藍＝你住的{home}，淺藍＝相鄰的區，灰色＝其他區，最淡＝沒有符合的課；"
           "區名下的數字是符合目前所有條件的課數。點一區只看那區的課，再點一次取消。")

# ── 課程清單 ──
area = scope(picked, st.session_state.picked_near)
shown = [c for c in hits if area is None or c["district"] in area]
where = f"{picked}和鄰近區" if picked and st.session_state.picked_near else picked
head = f"符合條件的課：{len(shown)} 門" + (f"（只看{where}）" if picked else "")
st.subheader(head)


def apply(**kv) -> None:
    for k, v in kv.items():
        st.session_state[k] = v


if picked:
    st.button("看全部行政區的課", on_click=apply, kwargs={"picked": None, "picked_near": False})

# 查無結果：列出「只放寬一個條件」各有幾門，點了直接套用
if not shown:
    def count(area_: set[str] | None, **change) -> int:
        return sum(1 for c in filtered({**params, **change}) if area_ is None or c["district"] in area_)

    here = "同區" if picked else ""
    options = []
    if picked and not st.session_state.picked_near:
        options.append(("鄰近區", count(scope(picked, True)), {"picked_near": True}))
    if picked:
        options.append(("全台南", count(None), {"picked": None, "picked_near": False}))
    for key, label in (("f_weekday", f"{here}其他星期"), ("f_slot", f"{here}其他時段"),
                       ("f_domain", f"{here}其他主題"), ("f_date", f"{here}其他日期")):
        if params[key]:
            options.append((label, count(area, **{key: []}), {key: []}))
    if not params["f_keep_no_time"]:
        options.append((f"{here}時間沒寫的課", count(area, f_keep_no_time=True), {"f_keep_no_time": True}))
    if not params["f_show_ended"]:
        options.append((f"{here}已結束的課", count(area, f_show_ended=True), {"f_show_ended": True}))
    options = [o for o in options if o[1]]
    st.markdown("**這個條件下沒有課。**" + ("可以試試放寬一個條件：" if options else "請放寬條件。"))
    for i, (label, n, change) in enumerate(options):
        st.button(f"{label}有 {n} 門", key=f"relax_{i}", on_click=apply, kwargs=change)
rows = []
for c in sorted(shown, key=lambda c: sort_key(c, today)):
    inf = "、".join(FIELD_ZH.get(f, f) for f in c["inferred_fields"])
    link = "　".join(f'<a href="{html.escape(u)}" target="_blank" rel="noopener">原文{i if i > 1 else ""}</a>'
                     for i, u in enumerate(c["source_urls"], 1))
    rows.append("<tr>" + "".join(f"<td>{x}</td>" for x in (
        html.escape(status_text(c, today)),
        html.escape(c["course_name"] or "")
        + (f'<br><span class="warn">⚠ {missing_text(c)}，{phone_html(c["center"], PHONES)}</span>'
           if missing_text(c) else "")
        + (f'<br><span class="warn">⚠ 推論（原文沒明寫）：{html.escape(inf)}</span>' if inf and SHOW_INFERRED else ""),
        html.escape(c["center"].replace("樂齡學習中心", "")), html.escape(date_text(c, today)), html.escape(when(c)), html.escape(c["venue"] or "沒寫"),
        html.escape(c["domain"] or "—"), link)) + "</tr>")
if rows:
    st.markdown('<table class="courses"><tr><th>狀態</th><th>課名</th><th>中心</th><th>日期</th><th>星期與時間</th><th>地點</th><th>主題</th>'
                '<th>原文</th></tr>' + "".join(rows) + "</table>", unsafe_allow_html=True)

# ── 側邊欄：資料涵蓋範圍 ──
st.sidebar.header("資料涵蓋範圍")
by_source: dict[str, int] = {}
for c in courses:
    by_source[c["source_type"]] = by_source.get(c["source_type"], 0) + 1
st.sidebar.markdown("**資料來源**\n" + "".join(f"- {SOURCE_ZH.get(k, k)}：**{n}** 門\n" for k, n in by_source.items()))
st.sidebar.markdown(f"- 中心：**{len({c['center'] for c in courses})}** / {len(centers)} 間\n- 課程：**{len(courses)}** 門\n"
                    f"- 沒寫日期、星期或時間：{sum(no_time(c) for c in courses)} 門\n"
                    f"- 資料更新：**{datetime.fromtimestamp(COURSES.stat().st_mtime):%Y-%m-%d}**\n"
                    f"- 貼文日期：{min(c['post_date'] for c in courses)} ～ {max(c['post_date'] for c in courses)}")
st.sidebar.caption("⚠ 表示原文沒寫日期或時間，或這個欄位是推論的（AI 讀圖或照月份展開日期），請點「原文」確認。"
                   if SHOW_INFERRED else "⚠ 表示原文沒寫上課日期或時間，請打電話向中心確認，或點「原文」看貼文。")
