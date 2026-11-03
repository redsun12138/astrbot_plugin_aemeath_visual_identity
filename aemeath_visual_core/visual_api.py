"""Public delegation API for Private Companion 6.5.6 / 7.0 image hosts."""
from __future__ import annotations

import time
import uuid
from pathlib import Path
from typing import Any

from .prompt import build_generation_prompt


class VisualIdentityAPI:
    """Stable boundary consumed by private_companion's image bridge."""

    def __init__(self, plugin: Any) -> None:
        self.plugin = plugin
        self._done: dict[str, dict[str, Any]] = {}

    def bridge_lifecycle_status(self) -> dict[str, Any]:
        return {"active": True}

    async def generate_for_companion(self, owner: Any, request: dict[str, Any]) -> dict[str, Any]:
        request = dict(request or {})
        request_id = str(request.get("request_id") or "").strip() or f"aemeath_{uuid.uuid4().hex}"
        if request_id in self._done:
            return dict(self._done[request_id])
        if not self.plugin.config.get("enabled", True):
            return {"handled": False, "reason": "aemeath_visual_disabled"}

        identity = self.plugin.store.identity()
        a_state = {
            "activity": request.get("activity") or "",
            "location": request.get("location") or "",
            "outfit": request.get("outfit") or "",
            "mood": request.get("emotion") or request.get("mood") or "",
        }
        session_key = str(
            request.get("session_key") or request.get("umo") or request.get("origin_key") or ""
        ).strip()
        context_text = ""
        memory: dict[str, Any] = {}
        if session_key:
            if hasattr(self.plugin.store, "get_visual_context"):
                context_text = self.plugin.store.get_visual_context(session_key)
            if hasattr(self.plugin.store, "get_visual_memory"):
                memory = self.plugin.store.get_visual_memory(session_key) or {}
        # A 最新字段优先；C 只补 A 缺失的字段。
        life_state = dict(a_state)
        for key in ("activity", "location", "outfit", "mood"):
            if not life_state.get(key) and memory.get(key):
                life_state[key] = memory[key]
        if context_text and hasattr(self.plugin, "state_extractor"):
            if any(not life_state.get(key) for key in ("activity", "location", "outfit")):
                extracted = await self.plugin.state_extractor.extract(context_text, life_state)
                if extracted:
                    for key in ("activity", "location", "outfit", "mood"):
                        if not life_state.get(key) and extracted.get(key):
                            life_state[key] = extracted[key]
                    if hasattr(self.plugin.store, "save_visual_memory"):
                        self.plugin.store.save_visual_memory(session_key, extracted)
            life_state["deepseek_context"] = context_text
        main_prompt = str(
            request.get("prompt_text")
            or request.get("prompt")
            or request.get("main_prompt")
            or request.get("request_text")
            or ((request.get("envelope") or {}).get("image_prompt") or {}).get("positive")
            or ""
        )
        scene = str(
            request.get("scene")
            or request.get("requested_scene_preset")
            or life_state.get("location")
            or ""
        )
        pose = str(request.get("action") or request.get("pose") or "")
        # B 的默认服装只在 A/C 都没有明确服装时使用。
        outfit = str(
            request.get("outfit")
            or life_state.get("outfit")
            or identity.get("variable", {}).get("current_outfit", "")
        )
        life_state["outfit"] = outfit
        prompt = build_generation_prompt(
            identity,
            scene=scene,
            pose=pose,
            outfit=outfit,
            extra=main_prompt,
            life_state=life_state,
        )
        refs = self.plugin.store.current_refs()
        paths = request.get("reference_image_paths")
        if not isinstance(paths, list):
            paths = [request.get("reference_image_path")]
        for value in paths:
            p = Path(str(value or ""))
            if value and p.is_file() and p not in refs:
                refs.append(p)
        size = ""
        params = request.get("parameters")
        if isinstance(params, dict) and params.get("width") and params.get("height"):
            size = f"{int(params['width'])}x{int(params['height'])}"
        if not refs:
            return {
                "handled": True,
                "backend": "aemeath-visual",
                "image_path": "",
                "note": "视觉身份参考图未导入，无法生成。",
                "metadata": {},
            }
        try:
            image_path, backend = await self.plugin.imagegen.generate(prompt, refs, size)
        except Exception as exc:
            return {
                "handled": True,
                "backend": "aemeath-visual",
                "image_path": "",
                "note": f"爱弥斯视觉生图失败：{str(exc)[:180]}",
                "metadata": {},
            }
        result = {
            "handled": True,
            "backend": f"aemeath-visual/{backend}",
            "image_path": str(image_path),
            "note": "已按视觉身份档案生成。",
            "metadata": {
                "reference_used": bool(refs),
                "request_id": request_id,
                "generated_at": time.time(),
            },
        }
        self._done[request_id] = dict(result)
        if len(self._done) > 200:
            self._done.pop(next(iter(self._done)))
        return result


def get_visual_identity_api() -> VisualIdentityAPI | None:
    return _active_api


_active_api: VisualIdentityAPI | None = None


def register_visual_api(api: VisualIdentityAPI) -> None:
    global _active_api
    _active_api = api
