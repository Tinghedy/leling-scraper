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

# 捲動偵測（Streamlit 本身抓不到捲動位置，所以用一個看不見的元件跑這段 JS），在 <body> 上切換兩個狀態：
#   data-filters-hidden：完整篩選區（.st-key-filters）捲出畫面 → 顯示釘在上方的摘要列
#   data-bar-open：摘要列出現後，往上捲（累積超過 THRESH）→ 展開標籤面板；往下捲 → 收起
# 狀態放在 <body> 上，Streamlit 重跑（點了標籤）時不會被重設，面板維持展開。
WATCH_JS = """
export default function() {
    const THRESH = 24;  // 累積捲動超過這麼多 px 才切換，避免手指微動就開開關關
    const update = (e) => {
        const f = document.querySelector(".st-key-filters");
        const hidden = !!f && f.getBoundingClientRect().bottom < 90;  // 90px ≈ 上方工具列＋一點緩衝
        document.body.toggleAttribute("data-filters-hidden", hidden);
        if (!hidden) { document.body.removeAttribute("data-bar-open"); return; }
        const el = !e || e.target === document ? document.scrollingElement : e.target;
        if (!el || typeof el.scrollTop !== "number") return;
        if (el.closest && el.closest(".st-key-summary_bar")) return;  // 面板自己的捲動不算
        const w = window.__lelingScroll, y = el.scrollTop;
        if (w.el !== el) { w.el = el; w.anchor = y; return; }
        const open = document.body.hasAttribute("data-bar-open");
        // 錨點記住「目前狀態下捲到最遠的位置」，只累積反方向的距離
        if (open) {
            if (y - w.anchor > THRESH) { document.body.removeAttribute("data-bar-open"); w.anchor = y; }
            else if (y < w.anchor) w.anchor = y;
        } else {
            if (w.anchor - y > THRESH) { document.body.setAttribute("data-bar-open", ""); w.anchor = y; }
            else if (y > w.anchor) w.anchor = y;
        }
    };
    if (!window.__lelingScroll) {  // 每次重跑都會呼叫，監聽只掛一次
        window.__lelingScroll = { el: null, anchor: 0 };
        document.addEventListener("scroll", update, { capture: true, passive: true });  // 捕捉任何捲動容器
        window.addEventListener("resize", () => update());
    }
    requestAnimationFrame(() => update());  // 重跑後版面可能變了，重新判斷一次
}
"""
scroll_watch = st.components.v2.component("filters_scroll_watch", js=WATCH_JS)
SHOW_INFERRED = False  # 清單裡要不要標「⚠ 推論（原文沒明寫）」；暫時隱藏
STATUS_ZH = {"upcoming": "即將開課", "ongoing": "進行中", "ended": "已結束", "unknown": "日期沒寫"}
# 結果列旁的狀態標籤（可複選；都不選＝不限）
STATUS_PILLS = {"ongoing": "進行中", "week": "本週有課", "upcoming": "即將開課"}
SOURCE_SHORT = {"moe_doc": "教育部公告", "fb_post": "Facebook"}
SOURCE_ZH = {"moe_doc": "教育部樂齡學習網公告", "fb_post": "FB 貼文"}

# ── 英文介面：只翻介面文字；課名、中心、地點是原文資料，不翻（搜尋時不能呼叫外部 API）──
WEEKDAYS_EN = dict(zip(WEEKDAYS, ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]))
SLOTS_EN = {"morning": "Morning", "afternoon": "Afternoon", "evening": "Evening"}
DOMAINS_EN = {ALL: "All", "數位科技": "Digital & Tech", "健康運動": "Health & Exercise", "藝術手作": "Arts & Crafts",
              "音樂表演": "Music & Performance", "語言文化": "Language & Culture", "生活財務": "Daily Life & Money",
              "社區志工": "Community & Volunteering"}
STATUS_EN = {"upcoming": "Starting soon", "ongoing": "Ongoing", "ended": "Ended", "unknown": "Dates not listed"}
STATUS_PILLS_EN = {"ongoing": "Ongoing", "week": "Classes this week", "upcoming": "Starting soon"}
SOURCE_SHORT_EN = {"moe_doc": "MOE notice", "fb_post": "Facebook"}
SOURCE_EN = {"moe_doc": "Ministry of Education lifelong-learning notices", "fb_post": "Facebook posts"}
DISTRICT_EN = {
    "七股區": "Qigu", "下營區": "Xiaying", "中西區": "West Central", "仁德區": "Rende", "佳里區": "Jiali",
    "六甲區": "Liujia", "北區": "North", "北門區": "Beimen", "南化區": "Nanhua", "南區": "South",
    "善化區": "Shanhua", "大內區": "Danei", "學甲區": "Xuejia", "安南區": "Annan", "安定區": "Anding",
    "安平區": "Anping", "官田區": "Guantian", "將軍區": "Jiangjun", "山上區": "Shanshang", "左鎮區": "Zuozhen",
    "後壁區": "Houbi", "新化區": "Xinhua", "新市區": "Xinshi", "新營區": "Xinying", "東區": "East",
    "東山區": "Dongshan", "柳營區": "Liuying", "楠西區": "Nanxi", "歸仁區": "Guiren", "永康區": "Yongkang",
    "玉井區": "Yujing", "白河區": "Baihe", "西港區": "Xigang", "關廟區": "Guanmiao", "鹽水區": "Yanshui",
    "麻豆區": "Madou", "龍崎區": "Longqi",
}


def is_en() -> bool:
    return st.session_state.get("lang") == "en"


def t(zh: str, en: str) -> str:
    """介面文字：依目前語言回傳中文或英文"""
    return en if is_en() else zh


def wd_label(w: str) -> str:
    return t("週" + w, WEEKDAYS_EN[w])


def slot_label(s: str) -> str:
    return t(SLOTS[s], SLOTS_EN[s])


def dom_label(d: str) -> str:
    return t(d, DOMAINS_EN.get(d, d))


def dist_label(d: str, short: bool = False) -> str:
    """英文：East District（地圖上只寫 East）"""
    if not is_en() or d not in DISTRICT_EN:
        return d
    return DISTRICT_EN[d] if short else f"{DISTRICT_EN[d]} District"


def pill_label(k: str) -> str:
    return t(STATUS_PILLS[k], STATUS_PILLS_EN[k])


def n_courses(n: int) -> str:
    return t(f"{n} 門", f"{n} course" + ("" if n == 1 else "s"))

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

# 語言：網址帶 ?lang=en 就直接開英文版（可以把英文版網址分享給別人）
st.session_state.setdefault("lang", "en" if st.query_params.get("lang") == "en" else "zh")
st.set_page_config(page_title=t("台南樂齡課程搜尋", "Tainan Senior Classes"), layout="wide")
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
.stSelectbox [role="combobox"], [data-testid="stExpander"] summary {
  min-height: var(--tap) !important; }
[data-testid="stCheckbox"] label, .stToggle label { min-height: var(--tap); display: flex; align-items: center; }

body, p, td, th, label { color: var(--text) !important; }
.stApp { background: var(--bg); }
/* 黃色底上的文字一律墨色，不用白字 */
button[kind^="primary"], button[kind^="primary"] p { color: var(--text) !important; }
button[kind^="primary"] { background: var(--primary) !important; border: 1.5px solid var(--ink) !important; }
/* 課程卡片：單欄直向排列 */
.card { border: 1.5px solid var(--ink); border-radius: 16px; padding: 16px 18px; margin: 0 0 16px; background: var(--card); }
.card .meta { color: var(--muted); font-size: 18px; margin-top: 8px; }
.card .name { font-weight: 700; font-size: 22px !important; line-height: 1.4; margin: 4px 0 8px; }
.card .row { display: flex; gap: 8px; align-items: flex-start; margin-top: 4px; }
.card .row .ms { color: var(--muted); }
.ms { font-family: "Material Symbols Rounded" !important; font-weight: normal; font-style: normal; font-size: 24px;
      line-height: 1.3; letter-spacing: normal; text-transform: none; white-space: nowrap; direction: ltr;
      -webkit-font-feature-settings: "liga"; font-feature-settings: "liga"; flex-shrink: 0; }
.card .row.warn, .card .row .ms.warn { color: var(--warn); }
.badge { display: inline-block; padding: 2px 14px; border-radius: 999px; font-weight: 700;
         border: 1.5px solid var(--ink); color: var(--text); }
.src { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 12px; }
.src-btn { display: inline-flex; align-items: center; gap: 6px; min-height: 48px; padding: 0 18px;
           border: 1.5px solid var(--ink); border-radius: 999px; background: var(--card);
           color: var(--text) !important; text-decoration: none !important; font-weight: 700; }
.src-btn:hover { background: var(--soft); }
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
/* 手機（≤ 640px）：地圖放在標題下方、「我住在」和篩選選項上面；桌機維持列表在左、地圖在右。
   同一個地圖元件只能畫一次，所以不在 Python 裡換位置，而是用 CSS：把包住地圖的 columns 外層都設成
   display: contents，地圖那一層就變成主欄的直接子元素，再用 order 排到最前面（標題用 order -2 留在最上面） */
@media (max-width: 640px) {
  [data-testid="stLayoutWrapper"]:has(> [data-testid="stHorizontalBlock"] .st-key-map_box),
  [data-testid="stHorizontalBlock"]:has(.st-key-map_box),
  [data-testid="stColumn"]:has(.st-key-map_box),
  [data-testid="stColumn"]:has(.st-key-map_box) > [data-testid="stVerticalBlock"] { display: contents !important; }
  [data-testid="stLayoutWrapper"]:has(> .st-key-map_box) { order: -1; }
  [data-testid="stElementContainer"]:has(h1) { order: -2; }
  [data-testid="stLayoutWrapper"]:has(> .st-key-lang_box) { order: -3; }
}
/* 語言切換：放在標題上方靠右，和標題之間不留大空白 */
.st-key-lang_box { margin-bottom: -12px; }
.st-key-w_lang button[aria-checked] { border: 1.5px solid var(--ink) !important; color: var(--text) !important;
  min-height: var(--tap) !important; padding: 0 18px !important; }
.st-key-w_lang button[aria-checked="false"] { background: var(--card) !important; }
.st-key-w_lang button[aria-checked="true"] { background: var(--primary) !important; }
.st-key-w_lang button[aria-checked="true"] p { font-weight: 700 !important; }
/* 摘要列（目前條件＋門數＋「修改條件」）：平常藏起來，完整篩選區捲出畫面後才出現、釘在上方。
   外層 stLayoutWrapper（主欄的直接子元素）設 sticky、高度 0，摘要列疊在內容上面，出現／消失都不會推動版面 */
[data-testid="stLayoutWrapper"]:has(> .st-key-summary_bar) { position: sticky; top: 3.75rem; z-index: 990;
                                                             height: 0; overflow: visible; }
.st-key-summary_bar { background: var(--bg); padding: 8px 0; border-bottom: 1.5px solid var(--ink);
                      box-shadow: 0 4px 8px rgba(36, 34, 35, 0.12);
                      opacity: 0; visibility: hidden; transform: translateY(-8px);
                      transition: opacity .2s, transform .2s, visibility 0s .2s; }
body[data-filters-hidden] .st-key-summary_bar { opacity: 1; visibility: visible; transform: none;
                                                transition: opacity .2s, transform .2s; }
.st-key-scroll_watch { display: none !important; }
.summary { margin: 0; font-size: 20px !important; }
.summary .ms { vertical-align: -5px; margin-right: 4px; }
.summary b { font-size: 22px; }
/* 往上捲時展開的標籤面板：grid-template-rows 0fr → 1fr 做真正的高度動畫（下面的東西不會瞬間跳），
   收 0.2 秒、開 0.25 秒，先快後慢；系統開了「減少動態效果」就直接切換 */
.st-key-summary_bar { gap: 0 !important; }
.st-key-summary_bar [data-testid="stMarkdownContainer"] { margin-bottom: 0 !important; }
.st-key-bar_panel { display: grid !important; grid-template-rows: 0fr; opacity: 0;
  transition: grid-template-rows .2s cubic-bezier(.4, 0, .2, 1), opacity .15s ease-out; }
.st-key-bar_panel > * { min-height: 0; overflow: hidden; }
body[data-bar-open] .st-key-bar_panel { grid-template-rows: 1fr; opacity: 1;
  transition: grid-template-rows .25s cubic-bezier(.2, 0, 0, 1), opacity .2s ease-out .05s; }
.st-key-bar_panel_inner { padding-top: 8px; max-height: 60vh; overflow: hidden; }
body[data-bar-open] .st-key-bar_panel_inner { overflow-y: auto; }  /* 收起來時不能捲，不然會吃掉頁面的捲動 */
.st-key-bar_panel_inner .stButtonGroup label, .st-key-bar_panel_inner [data-testid="stWidgetLabel"] { margin-bottom: 0; }
.summary .hint { color: var(--muted); font-size: 18px; white-space: nowrap; }
body[data-bar-open] .summary .hint { display: none; }
.summary .nowrap { white-space: nowrap; }
@media (max-width: 640px) { .summary .hint { display: block; } .summary .hint .dot { display: none; } }
@media (prefers-reduced-motion: reduce) {
  .st-key-bar_panel, body[data-bar-open] .st-key-bar_panel, .st-key-summary_bar { transition: none !important; } }
.st-key-filters .stButtonGroup label, .st-key-filters [data-testid="stWidgetLabel"] { margin-bottom: 0; }
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
    return max((c.get("post_date") or "" for c in json.loads(COURSES.read_text(encoding="utf-8"))), default="")


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
    fs = min(11.0, 0.032 * float(vb.split()[2])) * (0.85 if is_en() else 1)  # 英文區名比較長，字稍小
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
        shown_name = dist_label(name, short=True)  # data-name 維持中文（點選時傳回 Python 用）
        groups.append(f'<g class="d {tier}{" picked" if name == picked else ""}" data-name="{name}" tabindex="0" '
                      f'role="button" aria-label="{dist_label(name)}{t("，", ", ")}{n_courses(n)}"><title>{dist_label(name)}{t("：", ": ")}{n_courses(n)}</title>{paths}</g>')
        x, y = d["label"]
        labels.append(f'<text class="t {tier.replace("home zero", "zero")}" x="{x}" y="{y}" {label_style}><tspan x="{x}" dy="-0.15em">{shown_name}</tspan>'
                      f'<tspan x="{x}" dy="1.15em">{n}</tspan></text>')
    return (f'<svg viewBox="{vb}" aria-label="{t("臺南市行政區地圖", "Map of Tainan districts")}">'
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
        return t("日期和時間都沒寫", "Dates and time not listed")
    if no_date:
        return t("上課日期沒寫", "Dates not listed")
    if no_clock:
        return t("上課時間沒寫", "Time not listed")
    return None


def phone_html(center: str, phones: dict[str, str]) -> str:
    """centers.csv 有 phone 欄才顯示可撥號的連結；目前沒有這個欄位，只寫「請打電話向中心確認」。"""
    tel = phones.get(center)
    ask = t("請打電話向中心確認", "please call the center to confirm")
    if not tel:
        return ask
    digits = "".join(ch for ch in tel if ch.isdigit() or ch == "+")
    return f'{ask}{t("：", ": ")}<a href="tel:{html.escape(digits)}">{html.escape(tel)}</a>'


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
    if c["weekday"]:
        days = t("週" + "、".join(c["weekday"]), ", ".join(WEEKDAYS_EN[w] for w in c["weekday"]))
    else:
        days = t("星期沒寫", "Day not listed")
    clock = f"{c['start_time']}–{c['end_time'] or '?'}" if c["start_time"] else t("時間沒寫", "time not listed")
    return f"{days} {clock}"


for k, v in (("f_weekday", []), ("f_slot", []), ("f_domain", []), ("home", None), ("home_edit", False), ("f_show_ended", False), ("f_status", []), ("f_all", False), ("picked", None)):
    st.session_state.setdefault(k, v)



def sync_domain(src: str = "w_domain") -> None:
    """主題標籤的「全部」只是呈現：選「全部」＝清掉其他主題；選了其他主題就取消「全部」。篩選仍看 f_domain。
    src：哪一組標籤被改了（頁面上的 w_domain，或「修改條件」裡的 pop_domain）"""
    sel = list(st.session_state[src] or [])
    prev = st.session_state.get("w_domain_prev", [ALL])
    if ALL in sel and ALL not in prev:
        sel = [ALL]
    elif ALL in sel and len(sel) > 1:
        sel = [d for d in sel if d != ALL]
    sel = sel or [ALL]
    st.session_state.w_domain = st.session_state.w_domain_prev = sel
    st.session_state.f_domain = [d for d in sel if d != ALL]


st.session_state.setdefault("w_domain", st.session_state.f_domain or [ALL])
def set_lang() -> None:
    """語言切換；不讓它被取消選取（再點一次目前的語言時維持原樣），並寫進網址方便分享"""
    if st.session_state.w_lang is None:
        st.session_state.w_lang = st.session_state.lang
        return
    st.session_state.lang = st.session_state.w_lang
    if st.session_state.lang == "en":
        st.query_params["lang"] = "en"
    else:
        st.query_params.pop("lang", None)


st.session_state.w_lang = st.session_state.lang
with st.container(key="lang_box", horizontal=True, horizontal_alignment="right"):
    st.segmented_control("語言 Language", ["zh", "en"], key="w_lang", on_change=set_lang,
                         format_func={"zh": "中文", "en": "English"}.get, label_visibility="collapsed")
st.title(t("台南樂齡課程搜尋", "Tainan Senior Learning Classes"))
if not COURSES.exists():
    st.warning(t("還沒有課程資料。請先執行 import_courses.py 產生 data/courses.json。",
                 "No course data yet. Run import_courses.py to create data/courses.json."))
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
# 我住在＋篩選條件（不釘住；釘住的是下面的摘要列）
with st.container(key="filters"):
    if st.session_state.home is None or st.session_state.home_edit:
        with st.container(key="home_banner"):
            b1, b2 = st.columns([3, 2], vertical_alignment="center")
            b1.markdown(t("**選你住的區，先看離家近的課**", "**Pick your district to see classes near home first**"))
            # 英文介面的選單同時列出中文區名（East District（東區）），對照地址比較方便
            b2.selectbox(t("我住在", "I live in"), DISTRICTS, key="w_home", on_change=set_home, label_visibility="collapsed",
                         index=DISTRICTS.index(st.session_state.home) if st.session_state.home else None,
                         format_func=lambda d: t(d, f"{dist_label(d)} ({d})"),
                         placeholder=t("請選擇行政區", "Choose a district"))
    else:
        st.button(t(f"我住在：{st.session_state.home}", f"I live in: {dist_label(st.session_state.home)}"),
                  key="home_chip", help=t("點一下可以改住的區", "Tap to change your district"),
                  on_click=lambda: st.session_state.update(home_edit=True))
    home = st.session_state.home

    st.markdown(t("**篩選條件**", "**Filters**"))
    # 選項一變就重跑：地圖數字和列表都用同一份 hits
    # 手機寬度（< 640px）Streamlit 會把 columns 改成直向排列
    # 膠囊標籤可複選；手機上自動換行（不做左右滑動）
    st.pills(t("星期", "Day"), WEEKDAYS, selection_mode="multi", key="f_weekday", format_func=wd_label)
    st.pills(t("時段", "Time of day"), list(SLOTS), selection_mode="multi", key="f_slot", format_func=slot_label)
    st.pills(t("主題", "Topic"), [ALL, *DOMAINS], selection_mode="multi", key="w_domain", on_change=sync_domain,
             args=("w_domain",), format_func=lambda d: f":material/{DOMAIN_ICONS[d]}: {dom_label(d)}")

st.toggle(t("顯示已結束的課", "Show ended classes"), key="f_show_ended")
if st.session_state.home:
    st.toggle(t("看全台南的課", "Show all of Tainan"), key="f_all")

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
        return [(dist_label(picked), rows)]
    if not home:
        return [(t("全台南", "All of Tainan"), rows)]
    out = [(dist_label(home), [c for c in rows if c["district"] == home]),
           (t("鄰近區", "Nearby districts"), [c for c in rows if c["district"] in near])]
    if area is None:
        out.append((t("其他區", "Other districts"), [c for c in rows if c["district"] not in mine]))
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


def icon(name: str, cls: str = "") -> str:
    """Material Symbols 圖示（Streamlit 已載入 Material Symbols Rounded 字型；和主題標籤的 :material/…: 同一套）"""
    return f'<span class="ms {cls}" aria-hidden="true">{name}</span>'


def badge(c: dict) -> tuple[str, str]:
    """（樣式, 文字）：進行中 · 下一堂 11/16、即將開課 · 11/20 開始、已結束、日期沒寫"""
    st_, nd = status(c, today), next_date(c, today)
    if st_ == "ongoing":
        nxt = t("今天上課", "Class today") if nd == today else t(f"下一堂 {short(nd)}", f"Next class {short(nd)}") if nd else ""
        return "ongoing", t("進行中", "Ongoing") + " · " + nxt
    if st_ == "upcoming":
        return "upcoming", t(f"即將開課 · {short(nd)} 開始", f"Starting soon · Starts {short(nd)}")
    return st_, t(STATUS_ZH[st_], STATUS_EN[st_])


def sessions_text(c: dict) -> str:
    ds = c.get("dates") or []
    if not ds:
        return t("日期沒寫", "Dates not listed")
    span = short(ds[0]) if len(ds) == 1 else f"{short(ds[0])}～{short(ds[-1])}"
    return t(f"共 {len(ds)} 堂（{span}）", f"{len(ds)} session{'' if len(ds) == 1 else 's'} ({span.replace('～', '–')})")


def back_button(key: str) -> None:
    """點了地圖上的某一區之後，回到原本範圍的按鈕"""
    st.button(t(f"回到我住的區和鄰近區（{home}）", f"Back to my district and nearby ({dist_label(home)})") if home
              else t("回到全台南的課", "Back to all of Tainan"), type="primary", key=key,
              use_container_width=True, on_click=apply, kwargs={"picked": None, "f_all": False})


def card(c: dict) -> str:
    """卡片：左上狀態徽章 → 主題 · 來源 → 課名 → 時間、堂數、地點（各有圖示）→ 警示 → 看原文按鈕"""
    kind, text = badge(c)
    src = t(SOURCE_SHORT.get(c["source_type"], ""), SOURCE_SHORT_EN.get(c["source_type"], ""))
    meta = " · ".join(x for x in (dom_label(c["domain"]) if c["domain"] else "", src) if x)
    links = "".join(f'<a class="src-btn" href="{html.escape(u)}" target="_blank" rel="noopener">'
                    f'{icon("open_in_new")}{t("看原文", "Original post")}{f" {i}" if len(c["source_urls"]) > 1 else ""}</a>'
                    for i, u in enumerate(c["source_urls"], 1))
    miss = missing_text(c)
    inf = "、".join(FIELD_ZH.get(f, f) for f in c["inferred_fields"])
    # 中心和地點是原文資料，英文介面也照原文顯示，前面加上英文區名方便辨認
    place = (t("", f"{dist_label(c['district'])} · ") if c.get("district") else "") + \
        f'{c["center"].replace("樂齡學習中心", "")} · {c["venue"] or t("地點沒寫", "venue not listed")}'
    return (f'<div class="card"><span class="badge s-{kind}">{html.escape(text)}</span>'
            + (f'<div class="meta">{html.escape(meta)}</div>' if meta else "")
            + f'<div class="name">{html.escape(c["course_name"] or "")}</div>'
            f'<div class="row">{icon("schedule")}<span>{html.escape(when(c))}</span></div>'
            f'<div class="row">{icon("calendar_month")}<span>{html.escape(sessions_text(c))}</span></div>'
            f'<div class="row">{icon("location_on")}<span>{html.escape(place)}</span></div>'
            + (f'<div class="row warn">{icon("warning", "warn")}<span>{miss}{t("，", "; ")}{phone_html(c["center"], PHONES)}</span></div>'
               if miss else "")
            + (f'<div class="row warn">{icon("warning", "warn")}<span>推論（原文沒明寫）：{html.escape(inf)}</span></div>'
               if inf and SHOW_INFERRED else "")
            + f'<div class="src">{links}</div></div>')


where = (t(f"只看{picked}", f"only {dist_label(picked)}") if picked
         else t("全台南", "all of Tainan") if area is None
         else t(f"{home}和鄰近區", f"{dist_label(home)} and nearby"))



def from_popover(kind: str) -> None:
    """「修改條件」面板裡的標籤用另一組 key（同一個 widget key 不能畫兩次），改了就寫回真正的篩選條件"""
    if kind == "domain":
        sync_domain("pop_domain")
    else:
        st.session_state[f"f_{kind}"] = list(st.session_state[f"pop_{kind}"] or [])


def summary_html() -> str:
    # 只列出有選的條件，摘要列才短（手機上盡量一行）；什麼都沒選就只寫區和門數
    sep = t("、", ", ")
    chosen = [sep.join(map(wd_label, st.session_state.f_weekday)),
              sep.join(map(slot_label, st.session_state.f_slot)),
              sep.join(map(dom_label, st.session_state.f_domain)),
              sep.join(map(pill_label, st.session_state.f_status))]
    parts = [dist_label(home) if home else t("全台南", "All of Tainan")] + [x for x in chosen if x]
    return (f'<p class="summary">{icon("location_on")}{html.escape(" · ".join(parts))}'
            + ' · <span class="nowrap">' + t(f'符合 <b>{len(shown)}</b> 門', f'<b>{len(shown)}</b> match') + '</span>'
            # 收起時提示怎麼展開（展開後用 CSS 藏起來；手機上自己一行）
            + f'<span class="hint"><span class="dot"> · </span>{icon("swipe_down")}'
              f'{t("往上滑可以改條件", "Scroll up to change filters")}</span></p>')


# 釘在上方的篩選列（見 WATCH_JS）：完整篩選區捲出畫面後出現。
#   往下捲＝收成一行摘要；往上捲＝標籤從這一行滑下來展開，可以直接改條件，不用捲回頂端。
#   收合／展開只在瀏覽器裡用 CSS 動畫切換（不經過 Python 重跑），所以很快。
scroll_watch(key="scroll_watch")
with st.container(key="summary_bar"):
    st.markdown(summary_html(), unsafe_allow_html=True)
    # 面板裡的標籤每次重跑前先對齊目前的條件（頁面上的標籤改了，這裡也要跟著）
    st.session_state.pop_weekday = list(st.session_state.f_weekday)
    st.session_state.pop_slot = list(st.session_state.f_slot)
    st.session_state.pop_domain = list(st.session_state.w_domain)
    # 兩層容器：外層用 grid 0fr ↔ 1fr 做高度動畫，內層是實際內容
    with st.container(key="bar_panel"), st.container(key="bar_panel_inner"):
        st.pills(t("星期", "Day"), WEEKDAYS, selection_mode="multi", key="pop_weekday", format_func=wd_label,
                 on_change=from_popover, args=("weekday",))
        st.pills(t("時段", "Time of day"), list(SLOTS), selection_mode="multi", key="pop_slot", format_func=slot_label,
                 on_change=from_popover, args=("slot",))
        st.pills(t("主題", "Topic"), [ALL, *DOMAINS], selection_mode="multi", key="pop_domain",
                 format_func=lambda d: f":material/{DOMAIN_ICONS[d]}: {dom_label(d)}",
                 on_change=from_popover, args=("domain",))

# 桌機：列表在左、地圖在右；手機（< 640px）columns 直向排列，列表就在篩選條件下方、地圖上方
col_list, col_map = st.columns([11, 9], gap="large")

# ── 課程列表 ──
with col_list:
    def status_pills() -> None:
        st.pills(t("狀態", "Status"), list(STATUS_PILLS), selection_mode="multi", key="f_status",
                 format_func=pill_label, label_visibility="collapsed")

    # 結果列：「符合 N 門 · 資料更新於 YYYY/MM/DD」；狀態標籤放在第一個分區小標題的同一行、靠右（右緣和卡片對齊）
    updated = data_updated(COURSES.stat().st_mtime).replace("-", "/") or t("不明", "unknown")
    st.markdown(t(f'<p class="result-line">符合 <b>{len(shown)}</b> 門（{html.escape(where)}）'
                  f'<span class="sep"> · </span>資料更新於 {updated}</p>',
                  f'<p class="result-line"><b>{len(shown)}</b> {"class" if len(shown) == 1 else "classes"} found '
                  f'({html.escape(where)})<span class="sep"> · </span>Data updated {updated}</p>'),
                unsafe_allow_html=True)
    n_no_time = sum(1 for c in shown if no_time(c))
    if n_no_time and not shown_unsure:
        st.caption(t(f"其中 {n_no_time} 門沒寫上課日期或時間，排在最後面。",
                     f"{n_courses(n_no_time)} without dates or times are listed last."))

    # 查無結果：列出「只放寬一個條件」各有幾門，點了直接套用（沒有小標題，狀態標籤自己一行靠右）
    if not shown:
        with st.container(horizontal=True, horizontal_alignment="right"):
            status_pills()
        def count(area_: set[str] | None, **change) -> int:
            return sum(1 for c in filtered({**params, **change}) if area_ is None or c["district"] in area_)

        here = t("同區", "Same district, ") if picked else ""
        options = []
        if picked and home:
            options.append((t(f"{home}和鄰近區", f"{dist_label(home)} and nearby"), count(mine),
                            {"picked": None, "f_all": False}))
        if area is not None:
            options.append((t("全台南", "All of Tainan"), count(None), {"picked": None, "f_all": True}))
        for key, zh, en in (("f_weekday", "其他星期", "other days"), ("f_slot", "其他時段", "other times"),
                            ("f_domain", "其他主題", "other topics")):
            if params[key]:
                options.append((here + t(zh, en), count(area, **{key: []}), {key: []}))
        if params["f_status"]:
            options.append((here + t("不限狀態", "any status"), count(area, f_status=[]), {"f_status": []}))
        if not params["f_show_ended"]:
            options.append((here + t("已結束的課", "ended classes"), count(area, f_show_ended=True),
                            {"f_show_ended": True}))
        options = [o for o in options if o[1]]
        st.markdown(t("**這個條件下沒有課。**", "**No classes match these filters.** ")
                    + (t("可以試試放寬一個條件：", "Try loosening one filter:") if options
                       else t("請放寬條件。", "Please loosen the filters.")))
        for i, (label, n, change) in enumerate(options):
            label = label if not is_en() or picked else label[:1].upper() + label[1:]
            st.button(t(f"{label}有 {n} 門", f"{label}: {n_courses(n)}"), key=f"relax_{i}", on_click=apply, kwargs=change)
    else:
        # 分區小標題；每區內依下一次上課日排序。一次顯示 PAGE 門（跨區累計），條件一變就回到前 PAGE 門
        PAGE = 20
        sig = repr((params, home, picked, area is None))
        if st.session_state.get("list_sig") != sig:
            st.session_state.list_sig, st.session_state.n_show = sig, PAGE
        budget = st.session_state.n_show
        for i, (title, items) in enumerate(sections):
            if i == 0:  # 第一個小標題靠左、狀態標籤靠右，同一行（放不下時標籤換到下一行）
                with st.container(horizontal=True, horizontal_alignment="distribute", vertical_alignment="center"):
                    st.markdown(f"#### {title}{t(' ', ' · ')}{n_courses(len(items))}", width="content")
                    status_pills()
            else:
                st.markdown(f"#### {title}{t(' ', ' · ')}{n_courses(len(items))}")
            st.markdown("".join(card(c) for c in items[:max(budget, 0)]), unsafe_allow_html=True)
            budget -= len(items)
        if budget < 0:
            rest = -budget
            st.button(t(f"再顯示 {min(PAGE, rest)} 門（還有 {rest} 門）",
                        f"Show {min(PAGE, rest)} more ({rest} left)"), on_click=apply,
                      kwargs={"n_show": st.session_state.n_show + PAGE})

    # 選了星期或時段時，沒寫星期／時間的課無法確定是否符合：另外收在預設收合的區塊
    if shown_unsure:
        with st.expander(t(f"另外 {len(shown_unsure)} 門時間沒寫，請打電話確認",
                           f"{len(shown_unsure)} more without a listed day or time — please call to confirm"),
                         expanded=False):
            for title, items in sectioned(shown_unsure):
                if items:
                    st.markdown(f"#### {title}{t(' ', ' · ')}{n_courses(len(items))}")
                    st.markdown("".join(card(c) for c in items), unsafe_allow_html=True)

# ── 地圖：行政區分層設色，預設放大到住的區和鄰近區 ──
# 手機（≤ 640px）：地圖移到篩選選項上面（見 CSS 的 .st-key-map_box）
with col_map, st.container(key="map_box"):
    if picked:  # 回到原本範圍的按鈕放在地圖正上方
        back_button("back_map")
    # 預設放大到住的區和鄰近區（加上點選的區）；開「看全台南的課」時看全圖
    focus = None if area is None else mine | (area or set())
    result = district_map(data=map_svg(home, near, counts, picked, focus), key="district_map",
                          on_clicked_change=lambda: None)
    if result.clicked:  # 點同一區＝取消，點別區＝換過去
        st.session_state.picked = None if result.clicked == picked else result.clicked
        st.rerun()
    st.caption(t((f"黃色＝你住的{home}，淡黃＝相鄰的區，" if home else "")
                 + "灰色＝其他區，灰字 0＝沒有符合的課；區名下的數字是符合目前所有條件的課數。點一區只看那區的課，再點一次取消。",
                 (f"Yellow = your district ({dist_label(home)}), light yellow = neighboring districts, " if home else "")
                 + "gray = other districts, gray 0 = no matching classes. The number under each name is how many "
                   "classes match all current filters. Tap a district to see only its classes; tap again to undo."))

# ── 頁尾：關於這份資料 ──
with st.expander(t("關於這份資料", "About this data")):
    by_source: dict[str, int] = {}
    for c in courses:
        by_source[c["source_type"]] = by_source.get(c["source_type"], 0) + 1
    st.markdown(t("**資料來源**\n", "**Sources**\n") + "".join(
        t(f"- {SOURCE_ZH.get(k, k)}：**{n}** 門\n", f"- {SOURCE_EN.get(k, k)}: **{n}**\n") for k, n in by_source.items()))
    n_centers, n_no = len({c['center'] for c in courses}), sum(no_time(c) for c in courses)
    upd, p0, p1 = (data_updated(COURSES.stat().st_mtime) or t("不明", "unknown"),
                   min(c['post_date'] for c in courses), max(c['post_date'] for c in courses))
    st.markdown(t(f"- 中心：**{n_centers}** / {len(centers)} 間\n"
                  f"- 課程：**{len(courses)}** 門（同一門課的重複公告已合併）\n"
                  f"- 沒寫日期、星期或時間：{n_no} 門\n"
                  f"- 資料更新：**{upd}**（資料中最新一筆貼文或公告的日期）\n"
                  f"- 貼文日期：{p0} ～ {p1}",
                  f"- Centers: **{n_centers}** of {len(centers)}\n"
                  f"- Classes: **{len(courses)}** (repeated announcements of the same class are merged)\n"
                  f"- Missing date, day or time: {n_no}\n"
                  f"- Data updated: **{upd}** (date of the newest post or notice in the data)\n"
                  f"- Post dates: {p0} – {p1}\n"
                  f"- Class names, centers and venues are shown as published, in Chinese."))
    st.caption(t("⚠ 表示原文沒寫日期或時間，或這個欄位是推論的（AI 讀圖或照月份展開日期），請點「原文」確認。",
                 "⚠ means the original post didn't list the date or time, or this field was inferred; "
                 "tap “Original post” to check.")
               if SHOW_INFERRED else
               t("⚠ 表示原文沒寫上課日期或時間，請打電話向中心確認，或點「原文」看貼文。",
                 "⚠ means the original post didn't list the date or time. Please call the center, "
                 "or tap “Original post” to read it."))

