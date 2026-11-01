"""用 DeepSeek 从会话上下文提取活动/地点/穿搭。"""
from __future__ import annotations

import json
import re
from typing import Any

import aiohttp


class StateExtractor:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    async def extract(self, context_text: str, known: dict[str, Any] | None = None) -> dict[str, Any]:
        key = str(self.config.get("deepseek_api_key", ""))
        if not key or not context_text.strip():
            return {}
        base = str(self.config.get("deepseek_base_url", "https://api.deepseek.com/v1")).rstrip("/")
        model = str(self.config.get("deepseek_model", "deepseek-chat"))
        known = known if isinstance(known, dict) else {}
        prompt = (
            "你是生活状态提取器。阅读最近对话，提取当前仍然有效的角色生活信息。"
            "只输出 JSON：activity, location, outfit, mood, confidence, evidence。"
            "只提取对话中明确出现或可直接推出的信息；没有就留空字符串，不要编造。"
            "优先最近几条；如果角色换了衣服/地点，以最新说法为准。"
            f"已知时间轴信息：{json.dumps(known, ensure_ascii=False)}。"
            f"最近对话：\n{context_text[:6000]}"
        )
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0.1,
            "max_tokens": 500,
            "response_format": {"type": "json_object"},
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{base}/chat/completions", headers=headers, json=payload,
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200:
                        return {}
            text = str(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "")
        except Exception:
            return {}
        text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.I)
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            return {}
        try:
            result = json.loads(text[start:end + 1])
        except Exception:
            return {}
        if not isinstance(result, dict):
            return {}
        return {k: result.get(k) for k in ("activity", "location", "outfit", "mood", "confidence", "evidence") if result.get(k)}
