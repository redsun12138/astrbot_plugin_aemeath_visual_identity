"""读取 astrbot_plugin_private_companion 的生活状态。

优先调用其现有的 _build_companion_scene_snapshot()，不复制生活状态逻辑。
读取失败时返回空状态，生图仍然可以按用户请求降级生成。
"""
from __future__ import annotations

import time
from datetime import datetime
from typing import Any


class LifeStateReader:
    def __init__(self, owner: Any, config: dict[str, Any]):
        self.owner = owner
        self.config = config
        self._cache: dict[str, Any] = {}
        self._cache_at = 0.0

    def _plugin(self) -> Any | None:
        context = getattr(self.owner, "context", None)
        name = str(self.config.get("life_state_plugin_name", "astrbot_plugin_private_companion"))
        getter = getattr(context, "get_registered_star", None)
        if not callable(getter):
            return None
        try:
            meta = getter(name)
        except Exception:
            return None
        if meta is None:
            return None
        if not bool(getattr(meta, "activated", True)):
            return None
        instance = getattr(meta, "star_cls", None) or meta
        if not hasattr(instance, "data") and not hasattr(instance, "_build_companion_scene_snapshot"):
            return None
        return instance

    async def read(self, force: bool = False) -> dict[str, Any]:
        ttl = max(5, int(self.config.get("life_state_cache_seconds", 30)))
        now = time.time()
        if not force and self._cache and now - self._cache_at < ttl:
            return self._cache
        plugin = self._plugin()
        scene: dict[str, Any] = {}
        if plugin is not None:
            builder = getattr(plugin, "_build_companion_scene_snapshot", None)
            if callable(builder):
                try:
                    candidate = builder()
                    if hasattr(candidate, "__await__"):
                        candidate = await candidate
                    if isinstance(candidate, dict):
                        scene = candidate
                except Exception:
                    scene = {}
        state = self._extract(scene)
        if plugin is not None:
            fallback = self._fallback(plugin)
            for key, value in fallback.items():
                if value and not state.get(key):
                    state[key] = value
        state["available"] = bool(state)
        state["fetched_at"] = now
        self._cache = state
        self._cache_at = now
        return state

    def _fallback(self, plugin: Any) -> dict[str, Any]:
        """scene_snapshot 缺字段时，直接复用 private_companion 的现有读取方法。"""
        data = getattr(plugin, "data", {})
        data = data if isinstance(data, dict) else {}
        result: dict[str, Any] = {}
        plan = data.get("daily_plan") if isinstance(data.get("daily_plan"), dict) else {}
        try:
            current, schedule_text, _ = plugin._scene_context_current_schedule(plan)
        except Exception:
            current, schedule_text = {}, ""
        if isinstance(current, dict):
            result["activity"] = str(current.get("activity") or "")
            result["mood"] = str(current.get("mood") or "")
            result["scene"] = str(schedule_text or "")
        try:
            detail = plugin._current_detail_snapshot_for_update()
        except Exception:
            detail = None
        if isinstance(detail, dict):
            result["activity"] = result.get("activity") or str(detail.get("summary") or "")
            result["location"] = str(detail.get("location") or "")
            result["scene"] = result.get("scene") or str(detail.get("summary") or "")
        try:
            story = plugin._current_story_plan_snapshot()
        except Exception:
            story = {}
        if isinstance(story, dict):
            result["activity"] = result.get("activity") or str(story.get("event") or "")
            result["mood"] = result.get("mood") or str(story.get("mood") or "")
            result["scene"] = result.get("scene") or str(story.get("summary") or "")
            result["topic"] = str(story.get("topic") or "")
        try:
            outfit = plugin._current_dialogue_outfit_override()
        except Exception:
            outfit = {}
        if isinstance(outfit, dict) and outfit.get("instruction"):
            result["outfit"] = f"对话最新服装：{outfit['instruction']}"
        try:
            location_text = plugin._current_location_state_text()
        except Exception:
            location_text = ""
        if location_text and not result.get("location"):
            result["location"] = str(location_text)
        daily_state = data.get("daily_state") if isinstance(data.get("daily_state"), dict) else {}
        if daily_state:
            result.setdefault("mood", str(daily_state.get("mood_bias") or ""))
            result.setdefault("location", str(daily_state.get("location") or ""))
        return {k: v for k, v in result.items() if isinstance(v, str) and v.strip()}

    def _extract(self, scene: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(scene, dict) or not scene:
            return {}
        state = scene.get("state") if isinstance(scene.get("state"), dict) else {}
        schedule = scene.get("schedule") if isinstance(scene.get("schedule"), dict) else {}
        location = scene.get("location") if isinstance(scene.get("location"), dict) else {}
        outfit = scene.get("outfit") if isinstance(scene.get("outfit"), dict) else {}
        weather = scene.get("weather") if isinstance(scene.get("weather"), dict) else {}
        sleep = scene.get("sleep") if isinstance(scene.get("sleep"), dict) else {}
        realtime = scene.get("realtime") if isinstance(scene.get("realtime"), dict) else {}
        activity = realtime.get("activity") if isinstance(realtime.get("activity"), dict) else {}

        outfit_text = ""
        if outfit.get("dialogue_instruction"):
            outfit_text = f"对话最新服装：{outfit['dialogue_instruction']}"
        elif outfit.get("description"):
            outfit_text = str(outfit["description"])
        elif outfit.get("profile") and isinstance(outfit["profile"], dict):
            outfit_text = "；".join(str(v) for v in outfit["profile"].values() if v)

        result = {
            "time": " ".join(x for x in (str(scene.get("date") or ""), str(scene.get("time") or "")) if x),
            "daypart": str(scene.get("daypart") or ""),
            "activity": str(activity.get("label") or schedule.get("activity") or schedule.get("text") or ""),
            "mood": str(state.get("mood") or schedule.get("mood") or ""),
            "energy": str(state.get("energy_label") or ""),
            "location": str(location.get("text") or location.get("coarse") or location.get("raw") or ""),
            "outfit": outfit_text,
            "weather": str(weather.get("text") or ""),
            "sleep": str(sleep.get("label") or ""),
            "scene": str(schedule.get("text") or ""),
            "topic": str((scene.get("visual") or {}).get("topic") or "") if isinstance(scene.get("visual"), dict) else "",
            "outfit_reference": str(outfit.get("reference_path") or ""),
            "captured_at": str(scene.get("captured_at") or ""),
            "source": "private_companion.scene_snapshot",
        }
        # 去掉空字段，避免把“没有记录”写成事实
        return {k: v for k, v in result.items() if isinstance(v, str) and v.strip()}

    def status_block(self, state: dict[str, Any]) -> str:
        if not state.get("available"):
            return "现在的生活状态暂时没读到。"
        labels = (
            ("time", "时间"), ("activity", "当前活动"), ("location", "当前地点"),
            ("outfit", "当前穿搭"), ("mood", "当前情绪"), ("energy", "精力"),
            ("weather", "天气"), ("sleep", "睡眠状态"),
        )
        lines = [f"{label}：{state[key]}" for key, label in labels if state.get(key)]
        missing = [label for key, label in (("activity", "活动"), ("location", "地点"), ("outfit", "穿搭")) if not state.get(key)]
        if missing:
            lines.append("未记录：" + "、".join(missing) + "（生图时由 AI 按当前时间/情绪即兴补足，不写回生活档案）")
        return "\n".join(lines)

    def prompt_block(self, state: dict[str, Any]) -> str:
        if not state.get("available"):
            return ""
        labels = (
            ("time", "时间"), ("activity", "当前活动"), ("location", "当前地点"),
            ("outfit", "当前穿搭"), ("mood", "当前情绪"), ("energy", "精力"),
            ("weather", "天气"), ("sleep", "睡眠状态"), ("scene", "生活片段"),
        )
        lines = [f"{label}：{state[key]}" for key, label in labels if state.get(key)]
        if not lines:
            return ""
        note = (
            "其中活动/地点/穿搭是生活记录缺失时按时间氛围做的艺术补足，"
            "不是已记录事实；不要向用户声称这些是记录内容。"
            if state.get("inferred") else
            "这些是生活插件提供的已有记录。"
        )
        return (
            "【当前生活状态｜只读取已有记录，不要编造】\n"
            + "\n".join(lines)
            + "\n" + note
            + "\n这张图应表现上述生活状态的当下瞬间；除用户明确要求改变的内容外，"
            "不要改变当前活动、地点、穿搭和情绪。固定外貌必须保持不变。"
            "如果用户只是问“在干嘛/穿什么/在哪”，只用文字回答，不要调用任何生图工具。"
        )

    def unavailable_note(self) -> str:
        return "生活状态暂时读取不到；本次只按固定身份生成，不编造当前活动、地点或穿搭。"
