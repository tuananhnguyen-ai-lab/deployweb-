import hashlib
import json
import os
import time
import urllib.error
import urllib.request

API_URL = "https://api.anthropic.com/v1/messages"
TIMEOUT_SECONDS = 20
CACHE_TTL_SECONDS = 600
CACHE_MAX_ITEMS = 200

LEVEL_RULES = {
    1: "Chỉ gợi ý hướng suy nghĩ, tối đa 3 gạch đầu dòng. KHÔNG đưa ra đoạn code sửa lỗi.",
    2: "Chỉ ra chính xác dòng hoặc khái niệm gây lỗi. KHÔNG viết lại toàn bộ code.",
    3: "Giải thích nguyên nhân, đưa ví dụ ngắn cách sửa. Trình bày theo các mục: Nguyên nhân, Cách sửa.",
}

BASE_PROMPT = (
    "Bạn là trợ lý học lập trình web cho người mới bắt đầu. "
    "Trả lời bằng tiếng Việt, ngắn gọn, thân thiện, không quá 250 từ."
)

_cache: dict[str, tuple[float, str]] = {}


class AIError(Exception):
    pass


def _cache_key(code: str, error: str, level: int) -> str:
    return hashlib.sha256(f"{level}\n{code}\n{error}".encode("utf-8")).hexdigest()


def explain_error(code: str, error: str, level: int) -> str:
    if level not in LEVEL_RULES:
        level = 1

    key = _cache_key(code, error, level)
    hit = _cache.get(key)
    if hit and time.time() - hit[0] < CACHE_TTL_SECONDS:
        return hit[1]

    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        raise AIError("Chưa cấu hình ANTHROPIC_API_KEY trong file .env")

    payload = {
        "model": os.getenv("AI_MODEL", "claude-haiku-4-5"),
        "max_tokens": 400,
        "system": f"{BASE_PROMPT}\n{LEVEL_RULES[level]}",
        "messages": [{"role": "user", "content": f"Code:\n{code or '(không có)'}\n\nLỗi / câu hỏi:\n{error}"}],
    }
    req = urllib.request.Request(
        API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "content-type": "application/json",
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SECONDS) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code in (401, 403):
            raise AIError("API key không hợp lệ hoặc không có quyền") from e
        raise AIError(f"AI trả về lỗi ({e.code}), thử lại sau") from e
    except (urllib.error.URLError, TimeoutError) as e:
        raise AIError("Không kết nối được AI, kiểm tra mạng rồi thử lại") from e

    answer = "\n".join(
        b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
    ).strip()
    if not answer:
        raise AIError("AI không trả lời, thử lại với câu hỏi khác")

    if len(_cache) >= CACHE_MAX_ITEMS:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.time(), answer)
    return answer
