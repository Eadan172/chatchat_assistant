"""Diagnose the Serper web-search key without printing it.

Usage:
  python scripts/win/09-check-serper.py
Reads SERPER_API_KEY, or serper_api_key from $DATA_DIR/secrets/ask.json.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = Path(os.getenv("DATA_DIR") or (ROOT / ".data"))


def resolve_key() -> tuple[str, str]:
    env_key = (os.getenv("SERPER_API_KEY") or "").strip()
    if env_key:
        return env_key, "env"
    path = DATA_DIR / "secrets" / "ask.json"
    if path.is_file():
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            payload = {}
        value = str(payload.get("serper_api_key") or "").strip()
        if value:
            return value, str(path)
    return "", "missing"


def hint(status: int) -> str:
    return {
        400: "请求被拒绝：额度用尽或参数不合法（到 serper.dev 看 Credits）",
        401: "密钥无效：重新复制 API Key",
        403: "无权限/被封禁：换 key 或换provider",
        429: "限流：降低频率，稍后再试",
    }.get(status, "网络或代理异常：检查代理，或直连重试")


def main() -> int:
    key, source = resolve_key()
    print(f"key source : {source}")
    if not key:
        print("key        : MISSING")
        print("fix        : 在「设置 → 密钥」填写 Serper API Key，或设置 SERPER_API_KEY")
        return 2
    print(f"key        : {key[:4]}… len={len(key)}")
    if key != key.strip() or " " in key or "\n" in key:
        print("warn       : 密钥含空格/换行，保存时会去掉，但请确认复制完整")

    body = {"q": "Dify 是什么", "num": 5}
    headers = {"X-API-KEY": key, "Content-Type": "application/json"}
    for label, trust_env in (("direct", False), ("system-proxy", True)):
        try:
            with httpx.Client(trust_env=trust_env, timeout=25) as client:
                response = client.post("https://google.serper.dev/search", json=body, headers=headers)
            text = response.text.replace(key, "***")[:400]
            print(f"[{label}] HTTP {response.status_code}")
            if response.status_code == 200:
                data = response.json()
                print(f"          organic={len(data.get('organic') or [])} credits={data.get('credits')}")
            else:
                print(f"          body={text}")
                print(f"          hint={hint(response.status_code)}")
        except Exception as exc:
            print(f"[{label}] error={type(exc).__name__}: {exc}")
            print(f"          hint={hint(0)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
