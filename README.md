# 台南樂齡中心課程貼文抓取

每天從 `centers.csv` 挑 10 間樂齡中心，抓它們 Facebook 上的貼文，並整理成表格。
最久沒抓的會優先抓，36 間大約 4 天輪完一次。之後再跑，就只會抓上次之後的新貼文。

底層用的是 [agentic-facebook](https://github.com/tjdwls101010/Agentic-Facebook)，也就是原本的 `scraper-for-facebook`，現在已經改名了。

## 一、安裝（只要做一次）

```bash
git clone https://github.com/Tinghedy/leling-scraper.git ~/leling-scraper
```


需要 macOS 和 Python 3.9 以上（macOS 內建的 `/usr/bin/python3` 就可以）。`agentic-facebook` 需要的 Python 3.11 由 uv 自動處理，不用另外裝。

```bash
# 1. 安裝 uv（如果還沒有）
curl -LsSf https://astral.sh/uv/install.sh | sh

# 2. 用 uv 把 agentic-facebook 裝成獨立工具（不要用 pip 裝進其他專案的環境）
uv tool install agentic-facebook

# 3. 讓它下載自己專用的瀏覽器
agentic-facebook setup

# 4. 登入：會跳出一個瀏覽器視窗，請手動登入 Facebook
agentic-facebook login

# 5. 確認狀態，顯示 ready（exit 0）就可以了
agentic-facebook status
```

> **建議用小帳號登入。** 自動化讀取 Facebook 違反它的服務條款，帳號有被要求驗證甚至停用的風險。
> 工具作者也建議不要用主帳號。小帳號只要能看到這些公開粉專就夠了。

## 二、中心清單

`centers.csv` 已經填好 36 間有 Facebook 的中心。網址來自[教育部樂齡學習網的臺南市頁面](https://moe.senioredu.moe.gov.tw/HomeSon/Tainan/TainanIndex)上各中心的官方連結。另外 3 間（安南、東山、六甲）沒有使用 Facebook，所以沒有列進來。`note` 欄位標示每間的類型：

| 類型 | 數量 | 說明 |
|---|---|---|
| 粉專 | 28 | 依日期抓取，之後只抓新貼文 |
| 社團 | 8 | 社團沒有日期篩選，所以改成抓最新的 N 則（`GROUP_POSTS_PER_RUN`，預設 50），重複的會自動去掉。**私密社團要先用登入的帳號加入才抓得到**。社團裡也會有成員自己的發文，所以課程表格更需要靠關鍵字篩選 |

要新增或修改中心時，用 Excel 或 Numbers 編輯，存檔時記得選 CSV 格式。如果拿到的是 `facebook.com/share/...` 這種分享連結，請先在瀏覽器打開，等它跳轉到粉專後，複製網址列上的實際網址再貼進來。

## 三、每天執行

```bash
cd ~/leling-scraper
python3 run_daily.py
```

只想抓某一間的話，用 `--only` 加上名稱裡的字：

```bash
python3 run_daily.py --only 東區
```

跑一次大約 20 到 40 分鐘，因為每間之間會隨機等 1.5 到 4 分鐘，降低被判定為異常的機率。
跑的過程中電腦不能睡眠。

### 結果在哪裡

| 檔案 | 內容 |
|---|---|
| `output/courses.csv` | 判定為課程相關的貼文（**主要看這個**） |
| `output/all_posts.csv` | 全部貼文，用來檢查有沒有漏掉的課程 |
| `run.log` | 每次執行的紀錄 |
| `raw/` | 原始 JSON 資料，請不要刪 |
| `state.json` | 記錄每間抓到哪一天，請不要刪 |

CSV 的欄位有：中心、發文時間（台灣時間）、課程相關、命中關鍵字、主題標記、內文、圖片數、心情數、留言數、分享數、類型、連結。

### 調整課程關鍵字

`run_daily.py` 最上面有 `COURSE_KEYWORDS`（判斷是不是課程貼文）和 `TOPICS`（主題分類），可以自由增刪。
改完之後不用重新抓，執行下面這行就會用新的關鍵字重新整理表格：

```bash
python3 run_daily.py --merge-only
```

其他可以調整的設定也都在檔案最上面，例如每天抓幾間、從哪一天開始抓、每間之間要等多久。

## 四、（選用）讓它每天自動跑

如果不想每天手動執行，可以用 cron 排程。在終端機輸入 `crontab -e`，加入這一行，代表每天早上 9:10 執行：

```
10 9 * * * cd ~/leling-scraper && /usr/bin/python3 run_daily.py >> cron.log 2>&1
```

注意兩件事：
- 時間到的時候電腦必須是開機、而且沒有睡眠的狀態。
- 腳本會自己到 `~/.local/bin` 找 `agentic-facebook`，所以 cron 的 PATH 很短也沒關係。
- 資料夾請放在家目錄（例如 `~/leling-scraper`）。如果放在「文件」或「桌面」，macOS 會擋住 cron 的存取權限。

## 疑難排解

腳本會依照 agentic-facebook 回傳的狀態自動處理，`run.log` 會寫出原因：

| 訊息 | 意思 | 怎麼做 |
|---|---|---|
| 登入過期 | session 失效了 | 執行 `agentic-facebook login` 重新登入，再重跑 |
| **安全檢查** | Facebook 懷疑這個帳號 | **立刻停止。** 用一般瀏覽器登入 Facebook 完成驗證，暫停幾天再抓。一直重試可能讓帳號被永久停用 |
| 這段期間沒有貼文 | 可能真的沒發文，也可能是 Facebook 改版造成解析失敗 | 如果很多間都這樣，執行 `agentic-facebook feed --limit 3` 測試；也抓不到的話，就執行 `uv tool upgrade agentic-facebook` 更新 |
| 網址無法存取 | 粉專不存在、被限制或已停用 | 確認網址是否正確。修正後，到 `state.json` 刪掉那間的紀錄，下次就會重抓 |
| 日期範圍可能沒抓完整 | 這次沒抓到最早的日期 | 不用處理，下次輪到它時會自動補抓 |

## 資料與隱私

抓下來的貼文（`raw/`、`output/`）和執行紀錄（`state.json`、`run.log`）含有其他人的姓名與內容，已經寫在 `.gitignore` 裡，**不會被上傳到 GitHub**。請不要把這些檔案公開。
