"""视觉模型：Claude / MiMo，只负责看图与比对。"""
from __future__ import annotations

import base64
import json
import re
from pathlib import Path
from typing import Any

import aiohttp


FEATURE_PROMPT = """只输出一个 JSON 对象，不要 Markdown，不要解释。
分析这张官方角色图，字段：
hair_color, hair_style, bangs, side_hair, back_hair, hair_ornament,
eye_color, eye_shape, face_style, outfit, distinctive_details。
要求：描述具体颜色、形状、位置、结构；无法确认写 "unknown"；禁止编造。"""


def _extract_json(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
    text = re.sub(r"\s*```$", "", text)
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        text = text[start : end + 1]
    return json.loads(text)


class VisionService:
    def __init__(self, config: dict[str, Any]):
        self.config = config

    async def analyze_identity(self, image: Path) -> dict[str, Any]:
        provider = str(self.config.get("vision_provider", "claude")).lower()
        if provider == "mimo":
            text = await self._mimo(image, FEATURE_PROMPT)
        else:
            text = await self._claude(image, FEATURE_PROMPT)
        return _extract_json(text)

    async def compare(self, image: Path, refs: list[Path]) -> dict[str, Any]:
        prompt = (
            "比较图一（用户图）与后续官方参考图，判断图一角色是否像同一个角色。"
            "只输出 JSON：same_character(0-1), hair, eyes, ornament, face, outfit, differences, conclusion。"
            "这是语义比较，不是人脸身份鉴定。"
        )
        provider = str(self.config.get("vision_provider", "claude")).lower()
        if provider == "mimo":
            text = await self._mimo_multi([image] + refs, prompt)
        else:
            text = await self._claude_multi([image] + refs, prompt)
        return _extract_json(text)

    async def plan(self, identity: dict[str, Any], request: str, life_state: dict[str, Any] | None = None) -> dict[str, Any]:
        """让文本模型决定这次生成什么场景/姿势/服装。"""
        fixed = identity.get("fixed", {})
        life = life_state if isinstance(life_state, dict) else {}
        life_text = "；".join(
            f"{label}={life[key]}" for key, label in (
                ("time", "时间"), ("activity", "活动"), ("location", "地点"),
                ("outfit", "穿搭"), ("mood", "情绪"), ("weather", "天气"),
            ) if life.get(key)
        ) or "未读取到生活状态"
        prompt = (
            "你是爱弥斯的视觉导演。根据角色身份和用户请求，决定这次生图的场景、姿势、服装。"
            "只输出 JSON：scene, pose, outfit, mood。不要输出解释。"
            f"角色固定特征：{json.dumps(fixed, ensure_ascii=False)}。"
            f"当前造型：{identity.get('variable', {}).get('current_outfit', '官方默认')}。"
            f"当前生活状态（已记录事实，不能编造或随意覆盖）：{life_text}。"
            "如果生活记录没有活动、地点或穿搭，不要使用官方默认舞台礼服；"
            "请根据时间、情绪、睡眠状态即兴一个合理的当下生活瞬间（例如深夜困倦=卧室/居家睡衣/靠床休息），"
            "并在结果中只用于本次画面，不要声称它是已记录事实。"
            f"用户请求：{request or '今天让我看看你'}。"
            "如果用户没有明确要求换地点、换衣服或换活动，必须沿用当前活动、地点和穿搭；"
            "只允许为画面补足姿势、镜头、光线和自然细节。"
            "服装可以是当前造型；姿势不要固定站立。"
        )
        text = await self._mimo_text(prompt)
        return _extract_json(text)

    async def _mimo_text(self, prompt: str) -> str:
        key = str(self.config.get("mimo_api_key", ""))
        base = str(self.config.get("mimo_base_url", "https://api.xiaomimimo.com/v1")).rstrip("/")
        model = str(self.config.get("mimo_model", "mimo-v2.6-flash"))
        if not key:
            return ""
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 500,
            "temperature": 0.4,
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/chat/completions", headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=90)) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    return ""
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()

    async def _claude(self, image: Path, prompt: str) -> str:
        return await self._claude_multi([image], prompt)

    async def _claude_multi(self, images: list[Path], prompt: str) -> str:
        key = str(self.config.get("claude_api_key", ""))
        base = str(self.config.get("claude_base_url", "https://qa.dashun9527.com")).rstrip("/")
        model = str(self.config.get("claude_model", "claude-sonnet-4-5"))
        if not key:
            raise RuntimeError("Claude API Key 未配置")
        content = []
        for p in images:
            content.append({
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/png",
                    "data": base64.b64encode(p.read_bytes()).decode(),
                },
            })
        content.append({"type": "text", "text": prompt})
        payload = {"model": model, "max_tokens": 1200, "messages": [{"role": "user", "content": content}]}
        headers = {
            "x-api-key": key,
            "Authorization": f"Bearer {key}",
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/v1/messages", headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=180)) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RuntimeError(f"Claude HTTP {resp.status}: {str(data)[:200]}")
        return "\n".join(
            part.get("text", "") for part in data.get("content", []) if part.get("type") == "text"
        ).strip()

    async def _mimo(self, image: Path, prompt: str) -> str:
        return await self._mimo_multi([image], prompt)

    async def _mimo_multi(self, images: list[Path], prompt: str) -> str:
        key = str(self.config.get("mimo_api_key", ""))
        base = str(self.config.get("mimo_base_url", "https://api.xiaomimimo.com/v1")).rstrip("/")
        model = str(self.config.get("mimo_model", "mimo-v2.6-flash"))
        if not key:
            raise RuntimeError("MiMo API Key 未配置")
        content = []
        for p in images:
            b64 = base64.b64encode(p.read_bytes()).decode()
            content.append({"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}})
        content.append({"type": "text", "text": prompt})
        payload = {
            "model": model,
            "messages": [{"role": "user", "content": content}],
            "max_tokens": 900,
            "temperature": 0.1,
            "thinking": {"type": "disabled"},
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        async with aiohttp.ClientSession() as session:
            async with session.post(f"{base}/chat/completions", headers=headers, json=payload,
                                    timeout=aiohttp.ClientTimeout(total=180)) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise RuntimeError(f"MiMo HTTP {resp.status}: {str(data)[:200]}")
        return str(data.get("choices", [{}])[0].get("message", {}).get("content", "") or "").strip()
