"""从 AstrBot 消息中提取图片并下载到本地。"""
from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Any

import aiohttp

import astrbot.api.message_components as Comp


def _source_from_component(comp: Any) -> str:
    data = getattr(comp, "data", None)
    if isinstance(data, dict):
        for key in ("url", "path", "file", "file_path", "image_path"):
            if data.get(key):
                return str(data[key])
    for key in ("url", "path", "file", "file_path", "image_path"):
        value = getattr(comp, key, None)
        if value:
            return str(value)
    return ""


def image_sources(event: Any) -> list[str]:
    sources = []
    message_obj = getattr(event, "message_obj", None)
    chain = getattr(message_obj, "message", None) if message_obj is not None else None
    if not chain:
        return sources
    for comp in chain:
        if isinstance(comp, Comp.Image):
            src = _source_from_component(comp)
            if src and src not in sources:
                sources.append(src)
    return sources


async def download_image(source: str, out_dir: Path) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"input_{uuid.uuid4().hex}.png"
    if source.startswith("http://") or source.startswith("https://"):
        async with aiohttp.ClientSession() as session:
            async with session.get(source, timeout=aiohttp.ClientTimeout(total=60)) as resp:
                if resp.status != 200:
                    raise RuntimeError(f"下载图片失败 HTTP {resp.status}")
                out.write_bytes(await resp.read())
        return out
    if source.startswith("file://"):
        source = source[7:]
    p = Path(source)
    if p.exists():
        out.write_bytes(p.read_bytes())
        return out
    raise RuntimeError(f"无法读取图片：{source[:120]}")
