"""
呼叫 Gemini（google-genai SDK 的 Interactions API）拿結構化輸出，給 extract.py 和 app.py 共用。

  - 金鑰：環境變數 GEMINI_API_KEY
  - 結構化輸出：response_format 帶 pydantic 模型的 JSON schema，回來的文字再用 pydantic 驗證，
    不符合就丟 SchemaError
  - 速率：每分鐘最多 REQUESTS_PER_MINUTE 次；遇到 429 照伺服器建議的時間（沒給就指數退避）等待後重試
  - store=False：不讓 Google 保留請求和回應
  - 用量：每次呼叫的 token 累計在 USAGE，結束時可以算花費

用 `python llm.py` 列出這把金鑰能用的模型（確認 MODEL 還在）。
"""

from __future__ import annotations

import base64
import re
import time

from google import genai
from google.genai import errors
from pydantic import BaseModel, ValidationError

MODEL = "gemini-3.6-flash"   # 目前 stable 的 Flash；最新的是 gemini-3.8-flash（標示 New）
REQUESTS_PER_MINUTE = 10     # 免費額度保守一點；付費帳號可以調高
MAX_RETRIES = 6
# 每百萬 token 的美元價格（Gemini 3.6 Flash 付費版，2026 年底前）；免費額度不收費
PRICE_INPUT, PRICE_OUTPUT = 0.75, 3.75

USAGE = {"requests": 0, "input_tokens": 0, "output_tokens": 0}
_client: genai.Client | None = None
_last_call = 0.0


class SchemaError(Exception):
    """模型回的 JSON 不符合 schema。"""


def client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client()  # 讀 GEMINI_API_KEY；沒設定會丟錯
    return _client


def image_part(data: bytes, mime_type: str) -> dict:
    return {"type": "image", "data": base64.b64encode(data).decode("ascii"), "mime_type": mime_type}


def _retry_delay(e: errors.APIError, attempt: int) -> float:
    """429 回應裡如果有 retryDelay（例如 "17s"）就照它，否則指數退避。"""
    m = re.search(r'"retryDelay":\s*"(\d+(?:\.\d+)?)s"', str(e.details or ""))
    return float(m.group(1)) + 1 if m else min(60.0, 5.0 * 2 ** attempt)


def generate(system: str, parts: list[dict], schema: type[BaseModel], model: str = MODEL) -> BaseModel:
    """送一次請求，回傳驗證過的 pydantic 物件。"""
    global _last_call
    for attempt in range(MAX_RETRIES):
        time.sleep(max(0.0, 60 / REQUESTS_PER_MINUTE - (time.time() - _last_call)))
        _last_call = time.time()
        try:
            interaction = client().interactions.create(
                model=model,
                system_instruction=system,
                input=parts,
                response_format={"type": "text", "mime_type": "application/json",
                                 "schema": schema.model_json_schema()},
                store=False,
            )
        except errors.ClientError as e:
            if e.code == 429 and attempt < MAX_RETRIES - 1:
                time.sleep(_retry_delay(e, attempt))
                continue
            raise
        except errors.ServerError:
            if attempt < MAX_RETRIES - 1:
                time.sleep(min(60.0, 5.0 * 2 ** attempt))
                continue
            raise
        USAGE["requests"] += 1
        u = interaction.usage
        if u:
            USAGE["input_tokens"] += u.total_input_tokens or 0
            USAGE["output_tokens"] += (u.total_output_tokens or 0) + (u.total_thought_tokens or 0)
        try:
            return schema.model_validate_json(interaction.output_text or "")
        except ValidationError as e:
            raise SchemaError(f"{e.error_count()} 個欄位不符合 schema") from e
    raise RuntimeError("重試次數用完")


def cost_usd() -> float:
    return (USAGE["input_tokens"] * PRICE_INPUT + USAGE["output_tokens"] * PRICE_OUTPUT) / 1_000_000


if __name__ == "__main__":
    for m in client().models.list():
        if "flash" in (m.name or ""):
            print(m.name)
