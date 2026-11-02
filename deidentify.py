"""
去識別化：在抓下來的資料寫進硬碟之前，先把可以認出個人的資訊拿掉。

原則是「只留做課程表需要的東西」：
  - 發文者：姓名、個人頁網址、Facebook 數字 ID 全部刪掉，
    改成固定的代號（例如「成員-3fa91c」）。同一個人每次都會得到同一個代號，
    所以還是能分辨「這幾則是同一個人發的」，但看不出是誰。
  - 圖片、影片：只留數量，不留網址（Facebook 的圖片網址可以直接看到長輩的照片）。
  - 內文：遮蔽手機號碼、Email、LINE ID、身分證字號、個人頁連結，
    以及「王阿嬤」「陳大哥」這類姓氏加稱謂的稱呼。
  - 社團裡「成員」發的非課程貼文：寫進硬碟前就把內文刪掉，只留發文時間和互動數。
    （之後就算改了課程關鍵字，這些已刪掉的內文也救不回來，這是刻意的取捨。）

代號用的是 HMAC-SHA256，金鑰存在 .pseudonym_key（第一次執行時自動產生，已列在
.gitignore）。沒有這把金鑰，就無法從代號反推回原本的 Facebook ID。
金鑰弄丟的話，之後的代號會跟以前對不上，但不影響課程資料本身。

限制：一般的人名（例如「王小明」）沒有可靠的規則可以抓，所以內文裡直接寫出的全名
可能還在。這也是為什麼成員的非課程貼文整段不留。
"""

from __future__ import annotations

import hashlib
import hmac
import re
import secrets
from pathlib import Path

KEY_FILE = Path(__file__).resolve().parent / ".pseudonym_key"


def _key() -> bytes:
    if not KEY_FILE.exists():
        KEY_FILE.write_text(secrets.token_hex(32), encoding="utf-8")
        try:
            KEY_FILE.chmod(0o600)
        except OSError:
            pass
    return bytes.fromhex(KEY_FILE.read_text(encoding="utf-8").strip())


def pseudonym(author_id: str | None, author_name: str | None = None) -> str:
    """同一個 ID 永遠得到同一個代號；沒有 ID 時退而用名字算。兩者都沒有就是「未知」。"""
    basis = (author_id or "").strip() or (author_name or "").strip()
    if not basis:
        return "未知"
    digest = hmac.new(_key(), basis.encode("utf-8"), hashlib.sha256).hexdigest()
    return f"成員-{digest[:6]}"


# ──────────────────────────── 內文遮蔽規則 ────────────────────────────

# 常見姓氏（約前 100 名），用來抓「姓氏 + 稱謂」，例如「王阿嬤」「陳大哥」
_SURNAMES = (
    "陳林黃張李王吳劉蔡楊許鄭謝洪郭邱曾廖賴徐周葉蘇莊呂江何蕭羅高潘簡朱鍾游彭詹胡施沈余"
    "盧梁趙顏柯翁魏孫戴范方宋鄧杜傅侯曹薛丁卓阮馬董溫唐藍石蔣古紀姚連馮歐程湯田康姜"
    "白汪鄒尤巫鐘黎涂龔嚴韓袁金童陸夏柳凃邵錢伍倪"
)
_HONORIFICS = (
    "阿嬤|阿媽|阿公|阿伯|阿姨|阿姑|阿桑|奶奶|爺爺|婆婆|伯伯|伯母|叔叔|嬸嬸|"
    "大哥|大姐|大姊|姐姐|姊姊|哥哥|媽媽|爸爸|先生|太太|小姐|女士|阿北"
)

# 中文字在 Python 的 \w 裡也算「字」，所以帳號、Email 這類規則一律用英數字元，
# 才不會把前面的中文句子一起吃掉。
_RULES: list[tuple[re.Pattern, str]] = [
    # 身分證字號
    (re.compile(r"(?<![A-Za-z0-9])[A-Z][12]\d{8}(?!\d)"), "[身分證已遮蔽]"),
    # Email
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)+"), "[Email已遮蔽]"),
    # 手機：0912-345-678、0912 345 678、0912345678、+886 912 345 678
    (re.compile(r"(?<!\d)(?:\+886[\s-]?|0)9\d{2}[\s-]?\d{3}[\s-]?\d{3}(?!\d)"), "[手機已遮蔽]"),
    # LINE ID：「LINE ID：abc_123」「line: abc」「賴：abc」
    (re.compile(r"(?i)(line\s*id|line|賴)\s*[:：]\s*@?[A-Za-z0-9_.-]{3,}"), r"\1：[LINE ID已遮蔽]"),
    # 個人頁連結（粉專、社團、活動的連結不動）
    (re.compile(r"https?://(?:www\.|m\.)?facebook\.com/(?:profile\.php\?id=\d+|people/\S+)"),
     "[個人頁連結已遮蔽]"),
    # 姓氏 + 稱謂：「王阿嬤」→「○阿嬤」，保留稱謂讓句子還看得懂
    (re.compile(rf"[{_SURNAMES}]({_HONORIFICS})"), r"○\1"),
]

# 室內電話（例如 06-2345678）通常是中心或學校的報名電話，課程資訊會用到，所以刻意保留。


def scrub_text(text: str | None) -> str:
    if not text:
        return ""
    for pattern, repl in _RULES:
        text = pattern.sub(repl, text)
    return text


# ──────────────────────────── 單則貼文 ────────────────────────────

_GROUP_POST_URL = re.compile(r"https?://(?:www\.|m\.)?facebook\.com/groups/[^/?#]+/(?:posts|permalink)/\d+/?")


def _clean_url(url: str | None, is_group: bool) -> str | None:
    """社團貼文只留「社團 + 貼文編號」形式的網址；其他形式可能夾帶個人 ID，就不留。"""
    if not url:
        return None
    if is_group:
        m = _GROUP_POST_URL.match(url)
        return m.group(0) if m else None
    return url.split("?")[0] if "profile.php" not in url else url


def sanitize_post(post: dict, is_group: bool, is_course=None) -> dict:
    """把 agentic-facebook 的一則貼文轉成去識別化的版本。已經處理過的貼文原樣回傳。

    is_course：判斷內文是不是課程貼文的函式。社團貼文不是課程的話，內文整段不留。
    """
    if post.get("_deidentified"):
        return post

    text = scrub_text(post.get("text"))
    text_dropped = False
    if is_group and is_course is not None and not is_course(text):
        text, text_dropped = "", True

    media = post.get("media") or []
    shared = post.get("shared_post")
    clean = {
        "_deidentified": True,
        "id": post.get("id"),
        "url": _clean_url(post.get("url"), is_group),
        "type": post.get("type") or "",
        "is_pinned": bool(post.get("is_pinned")),
        # 粉專頁面上的貼文都是中心自己發的；社團的發文者一律換成代號
        "author": "中心" if not is_group else pseudonym(post.get("author_id"), post.get("author_name")),
        "created_at": post.get("created_at"),
        "edited_at": post.get("edited_at"),
        "text": text,
        "text_dropped": text_dropped,
        "media_count": len(media),
        "media_kinds": sorted({(m or {}).get("kind") or "unknown" for m in media}),
        "links": [
            {"url": (link or {}).get("url"), "title": scrub_text((link or {}).get("title"))}
            for link in (post.get("links") or [])
        ],
        "reaction_count": post.get("reaction_count"),
        "comment_count": post.get("comment_count"),
        "share_count": post.get("share_count"),
        "shared_post": sanitize_post(shared, is_group, is_course) if isinstance(shared, dict) else None,
        "source": post.get("source"),
        "captured_at": post.get("captured_at"),
    }
    return clean


def sanitize_file(path: Path, is_group: bool, is_course=None) -> int:
    """就地改寫一個 raw JSON 檔。回傳處理了幾則。原始內容不會留下備份。"""
    import json

    items = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(items, list):
        items = [items]
    cleaned = [sanitize_post(p, is_group, is_course) for p in items if isinstance(p, dict)]
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(cleaned, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(path)
    return len(cleaned)
