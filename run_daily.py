#!/usr/bin/env python3
"""
台南樂齡中心 Facebook 課程貼文 — 每日批次抓取

每次執行：
  1. 從 centers.csv 挑出「最久沒抓」的 N 間（預設 10 間）
  2. 逐一呼叫 agentic-facebook fetch，中間隨機等待，降低被 Facebook 判定異常的機率
  3. 把所有抓過的貼文合併、去重，輸出成兩個 CSV：
       output/all_posts.csv   全部貼文
       output/courses.csv     判定為課程相關的貼文（用關鍵字篩選，可自行調整）

第一次抓某間中心時，從 SINCE 日期開始抓；之後再輪到它時，只抓上次之後的新貼文。

需要：Python 3.9+，以及已完成登入的 agentic-facebook（見 README.md）
"""

from __future__ import annotations

import csv
import json
import random
import shutil
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# ──────────────────────────── 可以調整的設定 ────────────────────────────

CENTERS_PER_RUN = 10               # 每天抓幾間
SINCE = "2025-01-01"               # 第一次抓某間中心時，從哪一天開始抓
MAX_POSTS_PER_CENTER = 300         # 每間最多抓幾則（安全上限）
GROUP_POSTS_PER_RUN = 50           # 社團沒有日期篩選，每次抓最新幾則
WAIT_BETWEEN_CENTERS = (90, 240)   # 每間之間隨機等待幾秒（最小, 最大）

# 判定「課程貼文」的關鍵字：內文出現任一個就算
COURSE_KEYWORDS = [
    "課程", "上課", "開課", "課表", "報名", "招生", "講座", "研習",
    "工作坊", "體驗班", "學員", "講師", "老師", "堂課", "第一堂", "成果",
]

# 課程主題標記：一則貼文可以同時有多個標記，純粹方便之後分類，可自由增刪
TOPICS = {
    "數位科技": ["手機", "智慧型手機", "平板", "電腦", "網路", "LINE", "line", "APP", "app",
              "數位", "AI", "拍照", "視訊", "詐騙", "Google", "YouTube"],
    "健康運動": ["健康", "運動", "體適能", "肌力", "伸展", "瑜珈", "太極", "舞蹈", "律動",
              "營養", "防跌", "失智", "預防"],
    "藝術手作": ["手作", "繪畫", "書法", "藝術", "編織", "拼布", "彩繪", "花藝", "陶", "DIY"],
    "音樂表演": ["歌唱", "唱歌", "音樂", "樂器", "烏克麗麗", "陶笛", "戲劇", "表演"],
    "語言文化": ["台語", "臺語", "英語", "英文", "日語", "日文", "閩南語", "文化", "歷史", "導覽"],
    "生活財務": ["理財", "法律", "安全", "防災", "交通", "消費", "長照", "退休"],
    "社區志工": ["志工", "社區", "代間", "共餐", "關懷"],
}

# ──────────────────────────── 以下通常不用改 ────────────────────────────

BASE = Path(__file__).resolve().parent
CENTERS_FILE = BASE / "centers.csv"
STATE_FILE = BASE / "state.json"
RAW_DIR = BASE / "raw"
OUT_DIR = BASE / "output"
LOG_FILE = BASE / "run.log"
TAIPEI = timezone(timedelta(hours=8))

# agentic-facebook 的 exit code（來自套件本身的 exits.py）
OK, ERROR, LOGIN_REQUIRED, CHECKPOINT, NO_RESULTS, TARGET_UNAVAILABLE, SINCE_UNCONFIRMED = 0, 1, 2, 3, 4, 5, 7


def log(msg: str) -> None:
    line = f"[{datetime.now(TAIPEI):%Y-%m-%d %H:%M:%S}] {msg}"
    print(line, flush=True)
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")


def find_cli() -> str:
    """cron 的 PATH 通常很短，所以除了 PATH 也找 uv / pipx 預設的安裝位置。"""
    found = shutil.which("agentic-facebook")
    if found:
        return found
    for p in (Path.home() / ".local/bin/agentic-facebook", Path("/opt/homebrew/bin/agentic-facebook")):
        if p.exists():
            return str(p)
    sys.exit("找不到 agentic-facebook。請先照 README.md 安裝並登入。")


def load_centers() -> list[dict]:
    if not CENTERS_FILE.exists():
        sys.exit(f"找不到 {CENTERS_FILE.name}，請先建立中心清單。")
    with CENTERS_FILE.open(encoding="utf-8-sig", newline="") as f:
        rows = [r for r in csv.DictReader(f) if (r.get("url") or "").strip()]
    for r in rows:
        r["name"] = (r.get("name") or "").strip() or r["url"].strip()
        r["url"] = r["url"].strip()
    if not rows:
        sys.exit("centers.csv 裡沒有任何網址。")
    return rows


def load_state() -> dict:
    if STATE_FILE.exists():
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    return {}


def save_state(state: dict) -> None:
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def pick_today(centers: list[dict], state: dict) -> list[dict]:
    """還沒抓過的優先，其次是最久沒抓的；標記為無法存取的跳過。"""
    candidates = [c for c in centers if state.get(c["url"], {}).get("status") != "unavailable"]
    candidates.sort(key=lambda c: state.get(c["url"], {}).get("last_success") or "")
    return candidates[:CENTERS_PER_RUN]


def slug(center: dict) -> str:
    """把網址變成檔名用的代號。profile.php?id=123 這種網址要保留 id，不然會撞名。"""
    url = center["url"].rstrip("/")
    if "id=" in url:
        tail = "id" + url.split("id=", 1)[1].split("&")[0]
    else:
        tail = url.split("/")[-1].split("?")[0]
    return "".join(ch for ch in tail if ch.isalnum() or ch in "-.") or "center"


def fetch_one(cli: str, center: dict, state: dict) -> int:
    info = state.setdefault(center["url"], {"name": center["name"]})
    info["name"] = center["name"]
    since = info.get("last_success") or SINCE
    # 檔名帶時間：同一天重跑也不會覆蓋掉先前抓到的資料
    out = RAW_DIR / f"{slug(center)}_{datetime.now(TAIPEI):%Y%m%d-%H%M%S}.json"

    if "/groups/" in center["url"]:
        # 社團沒有日期篩選：改抓最新 N 則，重複的在合併時去掉
        cmd = [cli, "group", center["url"],
               "--sort", "recent",
               "--limit", str(GROUP_POSTS_PER_RUN),
               "--output", str(out)]
        log(f"抓取 {center['name']}（社團，最新 {GROUP_POSTS_PER_RUN} 則）")
    else:
        cmd = [cli, "fetch", center["url"],
               "--since", since,
               "--limit", str(MAX_POSTS_PER_CENTER),
               "--output", str(out)]
        log(f"抓取 {center['name']}（{since} 之後）")
    proc = subprocess.run(cmd, capture_output=True, text=True)
    code = proc.returncode
    if proc.stderr.strip():
        log("  " + proc.stderr.strip().splitlines()[-1])

    today = date.today().isoformat()
    info["last_attempt"] = today
    info["last_exit_code"] = code

    if code in (OK, SINCE_UNCONFIRMED):
        n = count_posts(out)
        log(f"  ✓ {n} 則" + ("（日期範圍可能沒抓完整，下次會再補）" if code == SINCE_UNCONFIRMED else ""))
        # 只有完整抓到日期範圍才往前推進，沒抓完整的下次會從同一天再抓一次
        if code == OK:
            info["last_success"] = today
        info["status"] = "ok"
    elif code == NO_RESULTS:
        log("  ○ 這段期間沒有貼文（或解析失敗，見 README 疑難排解）")
        info["last_success"] = today
        info["status"] = "ok"
        out.unlink(missing_ok=True)
    elif code == TARGET_UNAVAILABLE:
        log("  ✗ 這個網址無法存取（不存在、被限制或已停用），之後會跳過。請確認網址。")
        info["status"] = "unavailable"
        out.unlink(missing_ok=True)
    else:
        log(f"  ✗ 失敗（exit {code}），下次會再試")
        out.unlink(missing_ok=True)
    save_state(state)
    return code


def count_posts(path: Path) -> int:
    try:
        return len(json.loads(path.read_text(encoding="utf-8")))
    except Exception:
        return 0


def to_taipei(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
        return dt.astimezone(TAIPEI).strftime("%Y-%m-%d %H:%M")
    except ValueError:
        return iso


def build_outputs(centers: list[dict]) -> tuple[int, int]:
    """把 raw/ 裡所有檔案合併、去重，輸出 CSV。每次都從頭重建，所以改了關鍵字重跑就會生效。"""
    name_by_slug = {slug(c): c["name"] for c in centers}
    posts: dict[str, dict] = {}
    for f in sorted(RAW_DIR.glob("*.json")):
        center_name = name_by_slug.get(f.stem.rsplit("_", 1)[0], f.stem)
        try:
            items = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            log(f"  略過無法讀取的檔案 {f.name}")
            continue
        for p in items:
            key = p.get("id") or p.get("url")
            if not key:
                continue
            p["_center"] = center_name
            posts[key] = p  # 較新的檔案覆蓋舊的（互動數會更新）

    rows = []
    for p in posts.values():
        text = (p.get("text") or "").strip()
        shared = p.get("shared_post") or {}
        if not text and isinstance(shared, dict):
            text = (shared.get("text") or "").strip()
        hits = [k for k in COURSE_KEYWORDS if k in text]
        topics = [t for t, kws in TOPICS.items() if any(k in text for k in kws)]
        rows.append({
            "中心": p["_center"],
            "發文時間": to_taipei(p.get("created_at")),
            "課程相關": "是" if hits else "",
            "命中關鍵字": "、".join(hits),
            "主題標記": "、".join(topics),
            "內文": text,
            "圖片數": len(p.get("media") or []),
            "心情數": p.get("reaction_count") or 0,
            "留言數": p.get("comment_count") or 0,
            "分享數": p.get("share_count") or 0,
            "類型": p.get("type") or "",
            "連結": p.get("url") or "",
        })
    rows.sort(key=lambda r: (r["中心"], r["發文時間"]), reverse=False)

    OUT_DIR.mkdir(exist_ok=True)
    fields = list(rows[0].keys()) if rows else ["中心", "發文時間", "課程相關", "內文", "連結"]
    courses = [r for r in rows if r["課程相關"]]
    for name, data in (("all_posts.csv", rows), ("courses.csv", courses)):
        # utf-8-sig：讓 Excel 直接打開中文不會亂碼
        with (OUT_DIR / name).open("w", encoding="utf-8-sig", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            w.writerows(data)
    return len(rows), len(courses)


def main() -> None:
    RAW_DIR.mkdir(exist_ok=True)
    centers = load_centers()

    if "--merge-only" in sys.argv:
        total, n_course = build_outputs(centers)
        log(f"重新整理完成：共 {total} 則貼文，其中 {n_course} 則課程相關")
        return

    cli = find_cli()
    state = load_state()
    if "--only" in sys.argv:
        # 只抓指定中心，例如：python3 run_daily.py --only 東區
        idx = sys.argv.index("--only")
        keyword = sys.argv[idx + 1] if idx + 1 < len(sys.argv) else ""
        today_list = [c for c in centers if keyword and keyword in c["name"]]
        if not today_list:
            sys.exit(f"centers.csv 裡找不到名稱包含「{keyword}」的中心。")
    else:
        today_list = pick_today(centers, state)
    if not today_list:
        log("沒有可抓的中心（全部都被標記為無法存取？請檢查 centers.csv 和 state.json）")
        return

    log(f"===== 開始：今天抓 {len(today_list)} 間（清單共 {len(centers)} 間）=====")
    for i, c in enumerate(today_list):
        code = fetch_one(cli, c, state)
        if code == LOGIN_REQUIRED:
            log("!! 登入過期。請手動執行：agentic-facebook login，登入後再重跑。今天先停止。")
            break
        if code == CHECKPOINT:
            log("!! Facebook 對這個帳號發出安全檢查。請立刻停止，先到瀏覽器登入 Facebook 解除，"
                "並暫停幾天再抓。重複嘗試可能讓帳號被永久停用。")
            break
        if i < len(today_list) - 1:
            wait = random.uniform(*WAIT_BETWEEN_CENTERS)
            log(f"  等待 {wait:.0f} 秒…")
            time.sleep(wait)

    total, n_course = build_outputs(centers)
    done = sum(1 for c in centers if state.get(c["url"], {}).get("last_success"))
    log(f"===== 完成：已抓過 {done}/{len(centers)} 間；累計 {total} 則貼文，其中 {n_course} 則課程相關 =====")
    log(f"結果在 {OUT_DIR / 'courses.csv'}")


if __name__ == "__main__":
    main()
