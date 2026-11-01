"""生图后端：gpt-image-2 优先，qwen-image-3.0 回退。"""
from __future__ import annotations

import base64
import json
import time
from pathlib import Path
from typing import Any

import aiohttp


class ImageGenerationError(RuntimeError):
    pass


class ImageGenerator:
    def __init__(self, config: dict[str, Any], output_dir: Path):
        self.config = config
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

    async def generate(self, prompt: str, refs: list[Path], size: str = "") -> tuple[Path, str]:
        errors = []
        if self.config.get("gpt_image_enabled", True):
            try:
                return await self._gpt(prompt, refs, size), "gpt-image-2"
            except Exception as exc:
                errors.append(f"gpt-image-2: {exc}")
        if self.config.get("qwen_image_enabled", True):
            try:
                return await self._qwen(prompt, refs, size), "qwen-image-3.0"
            except Exception as exc:
                errors.append(f"qwen-image-3.0: {exc}")
        raise ImageGenerationError("所有生图后端都失败：" + " | ".join(errors))

    def _new_path(self, suffix: str = ".png") -> Path:
        return self.output_dir / f"aemeath_{time.time_ns()}{suffix}"

    async def _gpt(self, prompt: str, refs: list[Path], size: str) -> Path:
        key = str(self.config.get("gpt_image_api_key", ""))
        base = str(self.config.get("gpt_image_base_url", "https://qacn.dashun9527.com/v1")).rstrip("/")
        model = str(self.config.get("gpt_image_model", "gpt-image-2"))
        size = size or str(self.config.get("image_size", "1024x1536"))
        if not key:
            raise ImageGenerationError("gpt-image API Key 未配置")
        headers = {"Authorization": f"Bearer {key}"}
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            if not refs:
                payload = {"model": model, "prompt": prompt, "n": 1, "size": size}
                async with session.post(f"{base}/images/generations", headers=headers, json=payload) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise ImageGenerationError(f"HTTP {resp.status}: {str(data)[:200]}")
            else:
                form = aiohttp.FormData()
                form.add_field("model", model)
                form.add_field("prompt", prompt)
                form.add_field("n", "1")
                form.add_field("size", size)
                field = "image" if len(refs) == 1 else "image[]"
                for ref in refs[:3]:
                    form.add_field(field, ref.read_bytes(), filename=ref.name, content_type="image/png")
                async with session.post(f"{base}/images/edits", headers=headers, data=form) as resp:
                    data = await resp.json(content_type=None)
                    if resp.status != 200:
                        raise ImageGenerationError(f"HTTP {resp.status}: {str(data)[:200]}")
        try:
            raw = data["data"][0]["b64_json"]
        except Exception as exc:
            raise ImageGenerationError(f"返回格式异常: {str(data)[:200]}") from exc
        path = self._new_path(".png")
        path.write_bytes(base64.b64decode(raw))
        return path

    async def _qwen(self, prompt: str, refs: list[Path], size: str) -> Path:
        key = str(self.config.get("qwen_image_api_key", ""))
        base = str(self.config.get("qwen_image_base_url", "https://dashscope.aliyuncs.com")).rstrip("/")
        model = str(self.config.get("qwen_image_model", "qwen-image-3.0"))
        size = (size or str(self.config.get("image_size", "1024x1536"))).lower().replace("x", "*")
        if not key:
            raise ImageGenerationError("qwen-image API Key 未配置")
        content = []
        for ref in refs[:3]:
            b64 = base64.b64encode(ref.read_bytes()).decode()
            content.append({"image": f"data:image/png;base64,{b64}"})
        content.append({"text": prompt})
        payload = {
            "model": model,
            "input": {"messages": [{"role": "user", "content": content}]},
            "parameters": {
                "n": 1,
                "size": size,
                "watermark": False,
                "prompt_extend": False,
            },
        }
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        timeout = aiohttp.ClientTimeout(total=300)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                f"{base}/api/v1/services/aigc/multimodal-generation/generation",
                headers=headers, json=payload,
            ) as resp:
                data = await resp.json(content_type=None)
                if resp.status != 200:
                    raise ImageGenerationError(f"HTTP {resp.status}: {str(data)[:200]}")
                url = data["output"]["choices"][0]["message"]["content"][0]["image"]
                async with session.get(url) as img_resp:
                    if img_resp.status != 200:
                        raise ImageGenerationError(f"下载图片失败 HTTP {img_resp.status}")
                    raw = await img_resp.read()
        path = self._new_path(".png")
        path.write_bytes(raw)
        return path
