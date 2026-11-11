"""
台南樂齡課程搜尋（部署在 Streamlit Community Cloud）。

  篩選（星期、時段、主題）＋「我住在」→ 課程列表＋行政區地圖（住的區深色、相鄰區淺色、其他灰色，
  每區標出符合所有條件的課數；點一區只看那區的課，再點一次取消）。條件一變就即時更新。
  依台北今天日期判斷課程狀態，預設只顯示即將開課和進行中的課，依下一次上課日排序；
  同一門課的重複公告在載入時合併（不改 data/courses.json）。

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
# 主題標籤的圖示：全站統一用 Material Symbols（Streamlit 內建的 :material/…: 語法）
ALL = "全部"
DOMAIN_ICONS = {ALL: "apps", "數位科技": "smartphone", "健康運動": "directions_run", "藝術手作": "palette",
                "音樂表演": "music_note", "語言文化": "translate", "生活財務": "savings", "社區志工": "volunteer_activism"}
FIELD_ZH = {"course_name": "課名", "weekday": "星期", "start_time": "開始時間", "end_time": "結束時間",
            "start_date": "開始日期", "end_date": "結束日期", "venue": "地點"}
# ── 色票：全站唯一一份（.streamlit/config.toml 的 [theme] 用同樣的值）──
# 對比（WCAG AA，一般文字 ≥ 4.5:1）見 README 或 commit 說明；黃色底上一律用墨色字，不用白字
THEME_CSS = """
:root {
  --bg: #FFFCF3;        /* 頁面背景 */
  --card: #FFFFFF;      /* 卡片背景 */
  --ink: #242223;       /* 外框、區界線 */
  --primary: #E8D575;   /* 選中的標籤、主要按鈕、我住的區、進行中徽章 */
  --soft: #FFF4CC;      /* 鄰近區、提示橫幅、主題標籤、即將開課徽章 */
  --soft-text: #7A5C00; /* 淡黃底上的文字 */
  --text: #1A1A1A;      /* 主要文字；黃色底上的文字 */
  --muted: #5C574D;     /* 次要文字（最淺只到這個灰） */
  --gray-bg: #F7F7F7;   /* 其他區、0 門的區、已結束徽章 */
  --warn: #D6331C;      /* 只用在警示文字和圖示 */
  --font: "Noto Sans TC", "PingFang TC", "Microsoft JhengHei", sans-serif;
  --tap: 48px;          /* 可點元件的最小高度 */
  --gap: 8px;           /* 可點元件之間的最小間距 */
}
"""
# 地圖：我住的區主色、鄰近區淡黃、其他區淺灰、0 門的區淺灰加灰字；區界線墨色；點選的區加粗墨框
# 顏色都讀上面的 CSS 變數（自訂屬性會穿過元件的 shadow DOM 繼承下來），括號裡是備用值
MAP_CSS = """
.map { max-width: 780px; margin: 0 auto; }
svg { width: 100%; height: auto; display: block; }
.d { cursor: pointer; outline: none; }
.a { stroke: var(--ink, #242223); stroke-width: 1; vector-effect: non-scaling-stroke; }
.l { fill: none; stroke: var(--ink, #242223); stroke-width: 1; vector-effect: non-scaling-stroke; }
.home .a { fill: var(--primary, #E8D575); } .near .a { fill: var(--soft, #FFF4CC); }
.other .a, .zero .a { fill: var(--gray-bg, #F7F7F7); }
.home.zero .a { fill: var(--primary, #E8D575); }
.t.zero { fill: var(--muted, #5C574D); }
.d:hover .a, .d:focus-visible .a { filter: brightness(0.92); }
.picked .a { stroke: var(--ink, #242223); stroke-width: 4; }
.t { font: 700 11px var(--font, "Noto Sans TC", sans-serif); text-anchor: middle;
     pointer-events: none; fill: var(--text, #1A1A1A); paint-order: stroke; stroke: var(--card, #FFFFFF); stroke-width: 3px; }
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
# 結果列旁的狀態標籤（可複選；都不選＝不限）
STATUS_PILLS = {"ongoing": "進行中", "week": "本週有課", "upcoming": "即將開課"}
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
# 使用者是長者和家屬：字放大（內文最小 18px，這裡用 20px）、行高 1.6、對比加深
st.markdown(f"<style>{THEME_CSS}</style>", unsafe_allow_html=True)
st.markdown("""<style>
html, body, [class*="css"], .stMarkdown, p, li, label, input, button, td, th { font-size: 20px !important; }
html, body, .stApp, p, li, label, button, input { font-family: var(--font) !important; }
p, li, label, .card { line-height: 1.6 !important; }
h1 { font-size: 32px !important; font-weight: 700 !important; }
h2, h3 { font-size: 26px !important; } h4 { font-size: 23px !important; }
/* 可點的元件至少 48px 高、彼此至少間隔 8px */
.stButton button, .stButtonGroup button,
.stSelectbox [role="combobox"], [data-testid="stExpander"] summary, .src a {
  min-height: var(--tap) !important; }
[data-testid="stCheckbox"] label, .stToggle label { min-height: var(--tap); display: flex; align-items: center; }

body, p, td, th, label { color: var(--text) !important; }
.stApp { background: var(--bg); }
/* 黃色底上的文字一律墨色，不用白字 */
button[kind^="primary"], button[kind^="primary"] p { color: var(--text) !important; }
button[kind^="primary"] { background: var(--primary) !important; border: 1.5px solid var(--ink) !important; }
.card { border: 1.5px solid var(--ink); border-radius: 16px; padding: 12px 14px; margin: 0 0 12px; background: var(--card); }
.card .name { font-weight: 700; font-size: 22px !important; }
.card div { margin-top: 4px; }
.card .k { color: var(--muted); }
.badge { display: inline-block; padding: 1px 10px; border-radius: 999px; font-weight: 700; margin-right: 8px;
         border: 1.5px solid var(--ink); color: var(--text); }
.s-today, .s-ongoing { background: var(--primary); } .s-upcoming { background: var(--soft); color: var(--soft-text); }
.s-ended, .s-unknown { background: var(--gray-bg); color: var(--muted); }
.warn { color: var(--warn); font-weight: 700; }
/* 膠囊標籤（st.pills）：未選＝白底墨框墨字；選中＝主色底墨框粗體，前面加勾（不只靠顏色區分） */
/* Streamlit 1.67 的 pills 是 .stButtonGroup 裡帶 aria-pressed 的 button */
.stButtonGroup button[aria-pressed] {
  border: 1.5px solid var(--ink) !important; border-radius: 999px !important; color: var(--text) !important;
  padding: 0 18px !important; min-height: var(--tap) !important; }
.stButtonGroup button[aria-pressed="false"] { background: var(--card) !important; }
.stButtonGroup button[aria-pressed="true"] { background: var(--primary) !important; }
.stButtonGroup button[aria-pressed="true"] p { font-weight: 700 !important; }
.stButtonGroup button[aria-pressed="true"] > div::before { content: "✓"; font-weight: 700; margin-right: 6px; }
.st-key-home_banner { background: var(--soft); border: 1.5px solid var(--ink); border-radius: 16px; padding: 12px 16px; }
.st-key-home_banner p { color: var(--soft-text) !important; }
.st-key-home_banner [role="combobox"], .st-key-home_banner [data-baseweb="select"] > div { background: var(--card) !important; }
.st-key-home_chip button { background: var(--primary) !important; border: 1.5px solid var(--ink) !important;
  border-radius: 999px !important; padding: 0 20px !important; }
.st-key-home_chip button p { font-weight: 700 !important; color: var(--text) !important; }
.st-key-home_chip button p::before { content: "✓ "; }
.result-line { font-size: 22px !important; margin: 0; }
.result-line b { font-size: 26px; }
.result-line .sep { color: var(--muted); }
.stButtonGroup [role="toolbar"] { flex-wrap: wrap !important; gap: var(--gap) !important; }
a { color: var(--text) !important; text-decoration: underline; }
</style>""", unsafe_allow_html=True)


@st.cache_data
def load_courses(mtime: float) -> list[dict]:
    return merge_courses(json.loads(COURSES.read_text(encoding="utf-8")))


@st.cache_data
def data_updated(mtime: float) -> str:
    """資料更新日期：courses.json 是課程清單，沒有記錄更新日期的欄位，
    所以用合併前全部資料中最新一筆貼文或公告的日期（post_date）。不用檔案修改時間（部署後會變成部署當天）。"""
    return max((c.get("post_date") or "" for c in json.loads(COURSES.read_text(encoding="utf-8"))), default="") or "不明"


@st.cache_data
def load_map() -> dict:
    return json.loads(MAP.read_text(encoding="utf-8"))


def view_box(focus: set[str] | None) -> str:
    """focus 這幾區的外框（加一點邊）；None＝全台南"""
    m = load_map()
    if not focus:
        return f"0 0 {m['width']} {m['height']}"
    boxes = [d["bbox"] for d in m["districts"] if d["name"] in focus]
    x0, y0 = min(b[0] for b in boxes), min(b[1] for b in boxes)
    x1, y1 = max(b[0] + b[2] for b in boxes), max(b[1] + b[3] for b in boxes)
    pad = 0.08 * max(x1 - x0, y1 - y0)
    return f"{x0 - pad:.1f} {y0 - pad:.1f} {x1 - x0 + 2 * pad:.1f} {y1 - y0 + 2 * pad:.1f}"


def map_svg(home: str, near: set[str], counts: dict[str, int], picked: str | None, focus: set[str] | None) -> str:
    """區名都來自 tainan_map.json（固定的 37 區），不含使用者輸入。"""
    m = load_map()
    vb = view_box(focus)
    # 字級用 viewBox 單位：放大時字跟著縮，螢幕上看起來差不多大，小區的區名才不會互相壓到
    fs = min(11.0, 0.032 * float(vb.split()[2]))
    label_style = f'style="font-size:{fs:.2f}px;stroke-width:{fs * 0.27:.2f}px"'
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
        labels.append(f'<text class="t {tier.replace("home zero", "zero")}" x="{x}" y="{y}" {label_style}><tspan x="{x}" dy="-0.15em">{name}</tspan>'
                      f'<tspan x="{x}" dy="1.15em">{n}</tspan></text>')
    return (f'<svg viewBox="{vb}" aria-label="臺南市行政區地圖">'
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


def matches(c: dict, wd: list, slot: list, dom: list) -> str | None:
    """"yes"＝星期、時段確定符合；"maybe"＝選了星期或時段，但這門課沒寫星期或時間，無法確定；None＝不符合"""
    if dom and c["domain"] not in dom:
        return None
    maybe = False
    if wd:
        if not c["weekday"]:
            maybe = True
        elif not set(c["weekday"]) & set(wd):
            return None
    if slot:
        if not c["start_time"]:
            maybe = True
        elif c["time_slot"] not in slot:
            return None
    return "maybe" if maybe else "yes"


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
    """未結束的照下一次上課日由近到遠；時間沒寫的課排在後面；已結束的放最後，最近結束的在前。"""
    if status(c, today) == "ended":
        return (2, -date.fromisoformat(c["dates"][-1]).toordinal(), c["center"])
    nd = next_date(c, today)
    return (1 if no_time(c) else 0, date.fromisoformat(nd).toordinal() if nd else 10**9, c["center"])


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


for k, v in (("f_weekday", []), ("f_slot", []), ("f_domain", []), ("home", None), ("home_edit", False), ("f_show_ended", False), ("f_status", []), ("f_all", False), ("picked", None)):
    st.session_state.setdefault(k, v)



def sync_domain() -> None:
    """主題標籤的「全部」只是呈現：選「全部」＝清掉其他主題；選了其他主題就取消「全部」。篩選仍看 f_domain。"""
    sel = list(st.session_state.w_domain or [])
    prev = st.session_state.get("w_domain_prev", [ALL])
    if ALL in sel and ALL not in prev:
        sel = [ALL]
    elif ALL in sel and len(sel) > 1:
        sel = [d for d in sel if d != ALL]
    sel = sel or [ALL]
    st.session_state.w_domain = st.session_state.w_domain_prev = sel
    st.session_state.f_domain = [d for d in sel if d != ALL]


st.session_state.setdefault("w_domain", st.session_state.f_domain or [ALL])
st.title("台南樂齡課程搜尋")
if not COURSES.exists():
    st.warning("還沒有課程資料。請先執行 import_courses.py 產生 data/courses.json。")
    st.stop()
courses = load_courses(COURSES.stat().st_mtime)
centers = load_centers()
PHONES = {r["name"]: r["phone"].strip() for r in centers if (r.get("phone") or "").strip()}

def set_home() -> None:
    st.session_state.home = st.session_state.w_home
    st.session_state.home_edit = False
    st.session_state.picked = None


# 我住在：還沒選＝淡黃提示橫幅＋選區；選了＝一顆選中狀態的標籤「我住在：東區」，點了可以改
# （選單用另一個 key，避免選單沒畫出來時 Streamlit 把 home 清掉）
if st.session_state.home is None or st.session_state.home_edit:
    with st.container(key="home_banner"):
        b1, b2 = st.columns([3, 2], vertical_alignment="center")
        b1.markdown("**選你住的區，先看離家近的課**")
        b2.selectbox("我住在", DISTRICTS, key="w_home", on_change=set_home, label_visibility="collapsed",
                     index=DISTRICTS.index(st.session_state.home) if st.session_state.home else None,
                     placeholder="請選擇行政區")
else:
    st.button(f"我住在：{st.session_state.home}", key="home_chip", help="點一下可以改住的區",
              on_click=lambda: st.session_state.update(home_edit=True))
home = st.session_state.home

st.markdown("**篩選條件**")
# 選項一變就重跑：地圖數字和列表都用同一份 hits
# 手機寬度（< 640px）Streamlit 會把 columns 改成直向排列
# 膠囊標籤可複選；手機上自動換行（不做左右滑動）
st.pills("星期", WEEKDAYS, selection_mode="multi", key="f_weekday", format_func=lambda w: "週" + w)
st.pills("時段", list(SLOTS), selection_mode="multi", key="f_slot", format_func=SLOTS.get)
st.pills("主題", [ALL, *DOMAINS], selection_mode="multi", key="w_domain", on_change=sync_domain,
         format_func=lambda d: f":material/{DOMAIN_ICONS[d]}: {d}")

st.toggle("顯示已結束的課", key="f_show_ended")
if st.session_state.home:
    st.toggle("看全台南的課", key="f_all")

today = today_tpe()
FILTER_KEYS = ("f_weekday", "f_slot", "f_domain", "f_show_ended", "f_status")
_d = date.fromisoformat(today)
WEEK_END = date.fromordinal(_d.toordinal() + 6 - _d.weekday()).isoformat()  # 本週日


def status_ok(c: dict, chosen: list) -> bool:
    """狀態標籤：進行中、本週有課（今天到本週日之間有上課日）、即將開課；選多個＝符合任一個"""
    if not chosen:
        return True
    return (("ongoing" in chosen and status(c, today) == "ongoing")
            or ("upcoming" in chosen and status(c, today) == "upcoming")
            or ("week" in chosen and any(today <= d <= WEEK_END for d in c.get("dates") or [])))


def filtered(p: dict, verdict: str = "yes") -> list[dict]:
    """p：FILTER_KEYS 對應的值。地圖、列表、放寬建議都經過這裡，條件才會一致。
    verdict="yes" 只要確定符合的課；"maybe" 只要沒寫星期或時間、無法確定的課。"""
    return [c for c in courses
            if matches(c, p["f_weekday"], p["f_slot"], p["f_domain"]) == verdict
            and (p["f_show_ended"] or status(c, today) != "ended") and status_ok(c, p["f_status"])]


params = {k: st.session_state[k] for k in FILTER_KEYS}
hits = filtered(params)            # 確定符合：地圖數字、標題門數只算這些
unsure = filtered(params, "maybe")  # 沒選星期和時段時一定是空的，就不分段

picked = st.session_state.picked
near = set(NEIGHBORS.get(home, [])) if home else set()
mine = {home} | near if home else set()
# 列表看哪些區：點了地圖上的區＝只看那區；還沒選住的區或開了「看全台南的課」＝全部；預設＝住的區＋鄰近區
area = {picked} if picked else None if (st.session_state.f_all or not home) else mine


def in_view(rows: list[dict]) -> list[dict]:
    return sorted((c for c in rows if area is None or c["district"] in area), key=lambda c: sort_key(c, today))


def sectioned(rows: list[dict]) -> list[tuple[str, list[dict]]]:
    if picked:
        return [(picked, rows)]
    if not home:
        return [("全台南", rows)]
    out = [(home, [c for c in rows if c["district"] == home]),
           ("鄰近區", [c for c in rows if c["district"] in near])]
    if area is None:
        out.append(("其他區", [c for c in rows if c["district"] not in mine]))
    return out


shown, shown_unsure = in_view(hits), in_view(unsure)
sections = sectioned(shown)
counts: dict[str, int] = {}
for c in hits:
    counts[c["district"]] = counts.get(c["district"], 0) + 1


def apply(**kv) -> None:
    for k, v in kv.items():
        st.session_state[k] = v
    if "f_domain" in kv:  # 放寬主題時，主題標籤也要跟著顯示「全部」
        st.session_state.w_domain = st.session_state.w_domain_prev = kv["f_domain"] or [ALL]


def card(c: dict) -> str:
    sk = "today" if next_date(c, today) == today else status(c, today)
    links = "　".join(f'<a href="{html.escape(u)}" target="_blank" rel="noopener">原文{i if i > 1 else ""}</a>'
                      for i, u in enumerate(c["source_urls"], 1))
    miss = missing_text(c)
    inf = "、".join(FIELD_ZH.get(f, f) for f in c["inferred_fields"])
    return (f'<div class="card"><span class="badge s-{sk}">{status_text(c, today)}</span>'
            f'<span class="name">{html.escape(c["course_name"] or "")}</span>'
            f'<div><span class="k">時間：</span>{html.escape(when(c))}</div>'
            f'<div><span class="k">日期：</span>{html.escape(date_text(c, today))}</div>'
            f'<div><span class="k">地點：</span>{html.escape(c["center"].replace("樂齡學習中心", ""))}'
            f'・{html.escape(c["venue"] or "沒寫")}</div>'
            + (f'<div><span class="k">主題：</span>{html.escape(c["domain"])}</div>' if c["domain"] else "")
            + (f'<div class="warn">⚠ {miss}，{phone_html(c["center"], PHONES)}</div>' if miss else "")
            + (f'<div class="warn">⚠ 推論（原文沒明寫）：{html.escape(inf)}</div>' if inf and SHOW_INFERRED else "")
            + f"<div>{links}</div></div>")


# 桌機：列表在左、地圖在右；手機（< 640px）columns 直向排列，列表就在篩選條件下方、地圖上方
col_list, col_map = st.columns([11, 9], gap="large")

# ── 課程列表 ──
with col_list:
    where = f"只看{picked}" if picked else "全台南" if area is None else f"{home}和鄰近區"
    # 結果列：「符合 N 門 · 資料更新於 YYYY/MM/DD」＋狀態標籤
    r1, r2 = st.columns([1, 1], vertical_alignment="center")
    r1.markdown(f'<p class="result-line">符合 <b>{len(shown)}</b> 門（{html.escape(where)}）'
                f'<span class="sep"> · </span>資料更新於 {data_updated(COURSES.stat().st_mtime).replace("-", "/")}</p>',
                unsafe_allow_html=True)
    r2.pills("狀態", list(STATUS_PILLS), selection_mode="multi", key="f_status", format_func=STATUS_PILLS.get,
             label_visibility="collapsed")
    n_no_time = sum(1 for c in shown if no_time(c))
    if n_no_time and not shown_unsure:
        st.caption(f"其中 {n_no_time} 門沒寫上課日期或時間，排在最後面。")
    if picked:
        st.button(f"回到我住的區和鄰近區（{home}）" if home else "回到全台南的課", type="primary",
                  use_container_width=True, on_click=apply, kwargs={"picked": None, "f_all": False})

    # 查無結果：列出「只放寬一個條件」各有幾門，點了直接套用
    if not shown:
        def count(area_: set[str] | None, **change) -> int:
            return sum(1 for c in filtered({**params, **change}) if area_ is None or c["district"] in area_)

        here = "同區" if picked else ""
        options = []
        if picked and home:
            options.append((f"{home}和鄰近區", count(mine), {"picked": None, "f_all": False}))
        if area is not None:
            options.append(("全台南", count(None), {"picked": None, "f_all": True}))
        for key, label in (("f_weekday", f"{here}其他星期"), ("f_slot", f"{here}其他時段"),
                           ("f_domain", f"{here}其他主題")):
            if params[key]:
                options.append((label, count(area, **{key: []}), {key: []}))
        if params["f_status"]:
            options.append((f"{here}不限狀態", count(area, f_status=[]), {"f_status": []}))
        if not params["f_show_ended"]:
            options.append((f"{here}已結束的課", count(area, f_show_ended=True), {"f_show_ended": True}))
        options = [o for o in options if o[1]]
        st.markdown("**這個條件下沒有課。**" + ("可以試試放寬一個條件：" if options else "請放寬條件。"))
        for i, (label, n, change) in enumerate(options):
            st.button(f"{label}有 {n} 門", key=f"relax_{i}", on_click=apply, kwargs=change)
    else:
        # 分區小標題；每區內依下一次上課日排序。一次顯示 PAGE 門（跨區累計），條件一變就回到前 PAGE 門
        PAGE = 20
        sig = repr((params, home, picked, area is None))
        if st.session_state.get("list_sig") != sig:
            st.session_state.list_sig, st.session_state.n_show = sig, PAGE
        budget = st.session_state.n_show
        for title, items in sections:
            st.markdown(f"#### {title} {len(items)} 門")
            st.markdown("".join(card(c) for c in items[:max(budget, 0)]), unsafe_allow_html=True)
            budget -= len(items)
        if budget < 0:
            rest = -budget
            st.button(f"再顯示 {min(PAGE, rest)} 門（還有 {rest} 門）", on_click=apply,
                      kwargs={"n_show": st.session_state.n_show + PAGE})

    # 選了星期或時段時，沒寫星期／時間的課無法確定是否符合：另外收在預設收合的區塊
    if shown_unsure:
        with st.expander(f"另外 {len(shown_unsure)} 門時間沒寫，請打電話確認", expanded=False):
            for title, items in sectioned(shown_unsure):
                if items:
                    st.markdown(f"#### {title} {len(items)} 門")
                    st.markdown("".join(card(c) for c in items), unsafe_allow_html=True)

# ── 地圖：行政區分層設色，預設放大到住的區和鄰近區 ──
with col_map:
    # 預設放大到住的區和鄰近區（加上點選的區）；開「看全台南的課」時看全圖
    focus = None if area is None else mine | (area or set())
    result = district_map(data=map_svg(home, near, counts, picked, focus), key="district_map",
                          on_clicked_change=lambda: None)
    if result.clicked:  # 點同一區＝取消，點別區＝換過去
        st.session_state.picked = None if result.clicked == picked else result.clicked
        st.rerun()
    st.caption((f"黃色＝你住的{home}，淡黃＝相鄰的區，" if home else "")
               + "灰色＝其他區，灰字 0＝沒有符合的課；區名下的數字是符合目前所有條件的課數。點一區只看那區的課，再點一次取消。")

# ── 頁尾：關於這份資料 ──
with st.expander("關於這份資料"):
    by_source: dict[str, int] = {}
    for c in courses:
        by_source[c["source_type"]] = by_source.get(c["source_type"], 0) + 1
    st.markdown("**資料來源**\n" + "".join(f"- {SOURCE_ZH.get(k, k)}：**{n}** 門\n" for k, n in by_source.items()))
    st.markdown(f"- 中心：**{len({c['center'] for c in courses})}** / {len(centers)} 間\n"
                f"- 課程：**{len(courses)}** 門（同一門課的重複公告已合併）\n"
                f"- 沒寫日期、星期或時間：{sum(no_time(c) for c in courses)} 門\n"
                f"- 資料更新：**{data_updated(COURSES.stat().st_mtime)}**（資料中最新一筆貼文或公告的日期）\n"
                f"- 貼文日期：{min(c['post_date'] for c in courses)} ～ {max(c['post_date'] for c in courses)}")
    st.caption("⚠ 表示原文沒寫日期或時間，或這個欄位是推論的（AI 讀圖或照月份展開日期），請點「原文」確認。"
               if SHOW_INFERRED else "⚠ 表示原文沒寫上課日期或時間，請打電話向中心確認，或點「原文」看貼文。")
