"""爱弥斯视觉身份记忆与自主生图。"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import zipfile
from pathlib import Path

import astrbot.api.message_components as Comp
from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.star import Context, Star
from astrbot.core.message.message_event_result import MessageChain

sys.path.insert(0, str(Path(__file__).resolve().parent))
from aemeath_visual_core.db import IdentityStore  # noqa: E402
from aemeath_visual_core.imagegen import ImageGenerator  # noqa: E402
from aemeath_visual_core.image_utils import download_image, image_sources  # noqa: E402
from aemeath_visual_core.life_state import LifeStateReader  # noqa: E402
from aemeath_visual_core.prompt import build_generation_prompt, identity_block  # noqa: E402
from aemeath_visual_core.state_extractor import StateExtractor  # noqa: E402
from aemeath_visual_core.vision import VisionService  # noqa: E402
from aemeath_visual_core.visual_api import VisualIdentityAPI, register_visual_api  # noqa: E402

try:
    from astrbot.core.utils.astrbot_path import get_astrbot_data_path
except Exception:  # 旧版 AstrBot
    def get_astrbot_data_path():
        return "data"

SING_PATTERNS = (
    "看看你", "让我看看你", "长什么样", "你长什么", "自拍", "拍一张",
    "拍张照", "发张照片", "发一张照片", "来张照片", "换装",
)
STATE_PATTERNS = (
    "在干嘛", "在做什么", "做什么呢", "穿什么", "穿搭", "在哪", "忙什么", "生活状态",
)
DSML_INVOKE_RE = re.compile(
    r'<\|DSML\|\s*invoke\s+name="([^"]+)"[^>]*>(.*?)</\|DSML\|\s*invoke>',
    re.S | re.I,
)
DSML_PARAM_RE = re.compile(
    r'<\|DSML\|\s*parameter\s+name="([^"]+)"[^>]*>(.*?)</\|DSML\|\s*parameter>',
    re.S | re.I,
)
DSML_BLOCK_RE = re.compile(r"<\|DSML\|.*?</\|DSML\|\s*calls>", re.S | re.I)


class AemeathVisualIdentityPlugin(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.config = config
        self.data_dir = Path(get_astrbot_data_path()) / "plugin_data" / "astrbot_plugin_aemeath_visual_identity"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store = IdentityStore(self.data_dir, Path(__file__).parent / "assets" / "official")
        self.vision = VisionService(config)
        self.state_extractor = StateExtractor(config)
        self.imagegen = ImageGenerator(config, self.data_dir / "images" / "generated")
        self.life = LifeStateReader(self, config)
        self._state_answers: dict[str, dict] = {}
        self._pending_image_tasks: dict[str, asyncio.Task] = {}
        self._dsml_image_tasks: set[str] = set()
        self._locks: dict[str, asyncio.Lock] = {}
        self.extension_api = VisualIdentityAPI(self)
        register_visual_api(self.extension_api)
        try:
            from astrbot.core.provider.register import llm_tools
            names = [str(getattr(tool, "name", "") or "") for tool in getattr(llm_tools, "func_list", ())]
            logger.info("[aemeath_visual] LLM 工具表: %s", ", ".join(n for n in names if n))
        except Exception as exc:
            logger.warning("[aemeath_visual] 读取 LLM 工具表失败: %s", exc)
        logger.info("[aemeath_visual] 视觉身份插件已加载，官方素材 %s 张", len(self.store.official_images()))

    def _lock(self, umo: str) -> asyncio.Lock:
        return self._locks.setdefault(umo, asyncio.Lock())

    @filter.on_llm_request()
    async def inject_identity(self, *args, **kwargs) -> None:
        event = args[0] if args else kwargs.get("event")
        req = args[1] if len(args) > 1 else kwargs.get("req")
        if event is None or req is None:
            return
        if not self.config.get("enabled", True) or not self.config.get("inject_identity", True):
            return
        prompt = getattr(req, "system_prompt", "") or ""
        if "【视觉身份】" not in prompt:
            req.system_prompt = prompt + "\n\n" + identity_block(self.store.identity(), compact=True)
        text = (event.message_str or "").strip()
        if any(p in text for p in SING_PATTERNS) and "aemeath_take_photo" not in (req.system_prompt or ""):
            req.system_prompt = (req.system_prompt or "") + (
                "\n\n【图片工具】用户明确要自拍、看穿搭或看现在的样子时，"
                "必须调用工具 `aemeath_take_photo`，把主插件生成的场景/服装写进 prompt，"
                "并传 reference_roles=[\"identity\"]。不要调用不存在的 `companion_v2_image_generate`，"
                "也不要只回文字说无法拍。"
            )
        if any(p in text for p in STATE_PATTERNS) and "【当前生活状态" not in (req.system_prompt or ""):
            try:
                life = await self.life.read()
                block = self.life.prompt_block(life)
                if block:
                    req.system_prompt = (req.system_prompt or "") + "\n\n" + block
            except Exception as exc:
                logger.warning("[aemeath_visual] 生活状态读取失败: %s", exc)

    @filter.on_llm_response()
    async def capture_state_answer(self, *args, **kwargs) -> None:
        """兼容 AstrBot 4.29.4 只传 event 的钩子签名。"""
        event = args[0] if args else kwargs.get("event")
        response = args[1] if len(args) > 1 else kwargs.get("response")
        if event is None or response is None:
            return
        text = (getattr(response, "completion_text", "") or "").strip()
        if "<|DSML|" in text:
            await self._handle_dsml_image(event, response, text)
            return
        if not self.config.get("enabled", True) or not self.config.get("deepseek_context_enabled", True):
            return
        asked = (event.message_str or "").strip()
        if not any(p in asked for p in STATE_PATTERNS):
            return
        if not text:
            return
        self._state_answers[event.unified_msg_origin] = {"text": text[:1500], "ts": time.time()}

    @staticmethod
    def _parse_dsml(text: str) -> tuple[str, dict[str, str]]:
        invoke = DSML_INVOKE_RE.search(text)
        if not invoke:
            return "", {}
        name = invoke.group(1).strip()
        params = {
            key.strip(): value.strip()
            for key, value in DSML_PARAM_RE.findall(invoke.group(2))
        }
        return name, params

    def _set_response_text(self, response, text: str) -> None:
        try:
            response.completion_text = text
        except Exception:
            pass
        chain = getattr(getattr(response, "result_chain", None), "chain", None)
        if isinstance(chain, list):
            for part in chain:
                if isinstance(getattr(part, "text", None), str):
                    part.text = text

    async def _handle_dsml_image(self, event, response, text: str) -> None:
        name, params = self._parse_dsml(text)
        self._set_response_text(response, "")
        if name != "companion_v2_image_generate" or not params.get("prompt"):
            return
        umo = event.unified_msg_origin
        if umo in self._dsml_image_tasks:
            return
        caption = "好，我拍给你看。"
        self._set_response_text(response, caption)
        self._dsml_image_tasks.add(umo)
        asyncio.create_task(self._send_dsml_image(umo, params))

    async def _send_dsml_image(self, umo: str, params: dict[str, str]) -> None:
        try:
            result = await self.extension_api.generate_for_companion(
                self,
                {
                    "prompt_text": params.get("prompt", ""),
                    "workflow_kind": "selfie",
                    "request_id": f"dsml_{time.time_ns()}",
                },
            )
            if isinstance(result, dict) and result.get("image_path"):
                await self.context.send_message(
                    umo,
                    MessageChain([Comp.Image.fromFileSystem(str(result["image_path"]))]),
                )
        except Exception as exc:
            logger.warning("[aemeath_visual] DSML 生图失败: %s", exc)
        finally:
            self._dsml_image_tasks.discard(umo)

    def _recent_state_answer(self, umo: str) -> str:
        item = self._state_answers.get(umo)
        if not item:
            return ""
        if time.time() - float(item.get("ts", 0)) > 900:
            self._state_answers.pop(umo, None)
            return ""
        return str(item.get("text") or "")

    async def _conversation_context(self, umo: str) -> str:
        """扩大上下文窗口，读取最近助手回复；只取文字，不带插件日志。"""
        manager = getattr(getattr(self, "context", None), "conversation_manager", None)
        if manager is None:
            return ""
        try:
            cid = await manager.get_curr_conversation_id(umo)
            conversation = await manager.get_conversation(umo, cid)
            raw = getattr(conversation, "history", "[]") if conversation is not None else "[]"
            items = json.loads(raw) if isinstance(raw, str) else list(raw)
        except Exception as exc:
            logger.warning("[aemeath_visual] 读取会话上下文失败: %s", exc)
            return ""
        texts = []
        for item in reversed(items[-20:]):
            if not isinstance(item, dict):
                continue
            role = str(item.get("role") or item.get("type") or "").lower()
            if role not in {"assistant", "bot", "ai"}:
                continue
            value = item.get("content") or item.get("message") or item.get("text")
            if isinstance(value, str):
                text = value.strip()
            elif isinstance(value, list):
                text = " ".join(
                    str(p.get("text") or p.get("content") or "")
                    for p in value if isinstance(p, dict)
                ).strip()
            else:
                text = ""
            if text:
                texts.append(text[:500])
            if len(texts) >= 6:
                break
        return "\n".join(reversed(texts))

    async def _generate(self, event: AstrMessageEvent, scene: str = "") -> None:
        umo = event.unified_msg_origin
        async with self._lock(umo):
            data = self.store.identity()
            life = await self.life.read() if self.config.get("life_state_enabled", True) else {}
            if not isinstance(life, dict):
                life = {}
            deepseek_context = (await self._conversation_context(umo)) or self._recent_state_answer(umo)
            if deepseek_context and hasattr(self.store, "save_visual_context"):
                self.store.save_visual_context(umo, deepseek_context)
            elif not deepseek_context and hasattr(self.store, "get_visual_context"):
                deepseek_context = self.store.get_visual_context(umo)
            if deepseek_context:
                life["deepseek_context"] = deepseek_context
                if deepseek_context not in scene:
                    scene = (scene + "；最近对话里她说：" + deepseek_context).strip("；")
            memory = self.store.get_visual_memory(umo) if hasattr(self.store, "get_visual_memory") else {}
            if memory:
                for key in ("activity", "location", "outfit", "mood"):
                    if not life.get(key) and memory.get(key):
                        life[key] = memory[key]
                life["context_memory_source"] = memory.get("source", "context_ai")
            if deepseek_context and any(not life.get(k) for k in ("activity", "location", "outfit")):
                extracted = await self.state_extractor.extract(deepseek_context, life)
                if extracted and hasattr(self.store, "save_visual_memory"):
                    self.store.save_visual_memory(umo, extracted)
                    for key in ("activity", "location", "outfit", "mood"):
                        if not life.get(key) and extracted.get(key):
                            life[key] = extracted[key]
                    life["context_memory_source"] = "context_ai"
            current = data.get("variable", {}).get("current_outfit", "官方默认")
            pose = ""
            planned_outfit = ""
            if self.config.get("planner_enabled", False):
                try:
                    plan = await self.vision.plan(data, scene or (event.message_str or ""), life)
                    scene = str(plan.get("scene") or scene or "").strip()
                    pose = str(plan.get("pose") or "").strip()
                    planned_outfit = str(plan.get("outfit") or current).strip()
                except Exception as exc:
                    logger.warning("[aemeath_visual] 场景规划失败: %s", exc)
            if not planned_outfit and not life.get("outfit"):
                hour = 0
                m = re.search(r"(\d{1,2}):\d{2}", str(life.get("time") or ""))
                if m:
                    hour = int(m.group(1))
                if hour >= 23 or hour < 6:
                    planned_outfit = "宽松的浅色居家睡衣"
                    pose = pose or "靠在床头或侧躺休息"
                    scene = scene or "深夜卧室"
                else:
                    planned_outfit = "舒适的日常居家服"
            prompt = build_generation_prompt(
                data, scene=scene, pose=pose, outfit=planned_outfit, life_state=life
            )
            refs = self.store.current_refs()
            outfit_ref = life.get("outfit_reference") if isinstance(life, dict) else ""
            if outfit_ref:
                p = Path(str(outfit_ref))
                if p.is_file() and p not in refs:
                    refs.append(p)
            if not refs:
                yield event.plain_result("还没有视觉参考图，请先导入官方素材。")
                return
            ref_strings = [str(p) for p in refs]
            cached = self.store.find_generation(prompt, ref_strings)
            if cached:
                yield event.chain_result([
                    Comp.Plain("这张之前拍过，给你～"),
                    Comp.Image.fromFileSystem(str(cached["path"])),
                ])
                return
            quality = ""
            try:
                candidates = max(1, min(4, int(self.config.get("candidates", 1))))
                best_path = None
                best_backend = ""
                best_score = -1.0
                used = 0
                for _ in range(candidates):
                    path, backend = await self.imagegen.generate(prompt, refs, str(self.config.get("image_size", "1024x1536")))
                    used += 1
                    score = -1.0
                    if self.config.get("quality_check", True):
                        try:
                            report = await self.vision.compare(path, refs)
                            raw = report.get("same_character", report.get("score", -1))
                            score = float(raw) if str(raw).replace(".", "", 1).isdigit() else -1.0
                        except Exception as exc:
                            logger.warning("[aemeath_visual] 质量检查失败: %s", exc)
                    if best_path is None or score > best_score:
                        best_path, best_backend, best_score = path, backend, score
                    threshold = float(self.config.get("quality_threshold", 0.6))
                    if score >= threshold:
                        break
                quality = f"，一致性 {best_score:.2f}" if best_score >= 0 else ""
                cost = used * float(self.config.get(
                    "gpt_image_cost" if best_backend == "gpt-image-2" else "qwen_image_cost",
                    0.18 if best_backend == "gpt-image-2" else 0.08,
                ))
                self.store.record_generation(prompt, ref_strings, best_backend, str(best_path), cost)
                activity = str(life.get("activity") or "").strip()
                caption = "给你看看现在的我～"
                if activity:
                    caption = f"刚在{activity}，顺手拍给你看看～"
                yield event.chain_result([
                    Comp.Plain(caption),
                    Comp.Image.fromFileSystem(str(best_path)),
                ])
            except Exception as exc:
                logger.error("[aemeath_visual] 生图失败: %s", exc)
                yield event.plain_result("这次没拍成，等一会儿再给你看～")

    async def _image_for_reply(self, event: AstrMessageEvent, scene: str):
        async for result in self._generate(event, scene):
            chain = getattr(result, "chain", None) or []
            for part in chain:
                if isinstance(part, Comp.Image):
                    return part
        return None

    @filter.on_decorating_result()
    async def append_generated_image(self, event=None) -> None:
        if event is None:
            return
        umo = event.unified_msg_origin
        task = self._pending_image_tasks.pop(umo, None)
        if task is None:
            return
        try:
            image = await asyncio.wait_for(task, timeout=120)
        except Exception as exc:
            logger.warning("[aemeath_visual] 图片未在回复前完成: %s", exc)
            return
        if image is None:
            return
        try:
            result = event.get_result()
            if result is not None and getattr(result, "chain", None) is not None:
                result.chain.append(image)
            else:
                await self.context.send_message(umo, MessageChain([image]))
        except Exception as exc:
            logger.warning("[aemeath_visual] 追加图片失败: %s", exc)

    @filter.llm_tool(name="aemeath_take_photo")
    async def aemeath_take_photo(
        self,
        event: AstrMessageEvent,
        prompt: str = "",
        reference_ids: list[str] | None = None,
        reference_indices: list[int] | None = None,
        reference_roles: list[str] | None = None,
        **kwargs,
    ) -> str:
        """为爱弥斯生成一张带官方参考图的当前生活照片。

        Args:
            prompt(string): 主插件已经决定好的画面描述，包含活动、地点、服装、动作和镜头。
            reference_ids(array): 主插件提供的身身份参考图 ID；视觉插件会使用自己的官方参考图。
            reference_indices(array): 本轮聊天附图索引，通常为空。
            reference_roles(array): 参考用途，通常为 identity。
        """
        if not self.config.get("enabled", True):
            return json.dumps({"status": "disabled", "message": "视觉插件未启用"}, ensure_ascii=False)
        ids = reference_ids or kwargs.get("reference_ids") or []
        if isinstance(ids, str):
            ids = [ids]
        result = await self.extension_api.generate_for_companion(
            self,
            {
                "prompt_text": prompt,
                "workflow_kind": "edit",
                "request_id": f"tool_{time.time_ns()}",
                "session_key": event.unified_msg_origin,
                "reference_ids": ids,
                "reference_roles": reference_roles or kwargs.get("reference_roles") or ["identity"],
            },
        )
        path = result.get("image_path") if isinstance(result, dict) else ""
        if not path:
            return json.dumps(
                {"status": "failed", "message": result.get("note", "图片没有生成") if isinstance(result, dict) else "图片没有生成"},
                ensure_ascii=False,
            )
        await self.context.send_message(
            event.unified_msg_origin,
            MessageChain([Comp.Image.fromFileSystem(str(path))]),
        )
        return json.dumps(
            {"status": "success", "message": "图片已发送", "backend": result.get("backend", "")},
            ensure_ascii=False,
        )

    async def _send_image_later(self, event: AstrMessageEvent, scene: str) -> None:
        umo = event.unified_msg_origin
        try:
            image = await self._image_for_reply(event, scene)
            if image is not None:
                await self.context.send_message(umo, MessageChain([image]))
        except Exception as exc:
            logger.warning("[aemeath_visual] 后台发图失败: %s", exc)
        finally:
            self._pending_image_tasks.pop(umo, None)

    @filter.command("看看你")
    async def look_cmd(self, event: AstrMessageEvent):
        async for result in self._generate(event, (event.message_str or "")[4:].strip()):
            yield result

    @filter.command("自拍")
    async def selfie_cmd(self, event: AstrMessageEvent):
        async for result in self._generate(event, (event.message_str or "")[2:].strip()):
            yield result

    @filter.event_message_type(filter.EventMessageType.ALL)
    async def natural_look(self, event: AstrMessageEvent):
        if not self.config.get("enabled", True) or not self.config.get("natural_trigger", True):
            return
        if self.config.get("delegated_only", True):
            return
        text = (event.message_str or "").strip()
        if not text or text.startswith("/"):
            return
        sources = image_sources(event)
        if sources and any(k in text for k in ("像不像我", "是不是我", "这张图", "这个发饰", "和我一样吗")):
            event.stop_event()
            try:
                image = await download_image(sources[0], self.data_dir / "images" / "inputs")
                report = await self.vision.compare(image, self.store.current_refs())
                yield event.plain_result("像不像：" + json.dumps(report, ensure_ascii=False)[:900])
            except Exception as exc:
                yield event.plain_result(f"比对失败：{str(exc)[:180]}")
            return
        if not any(p in text for p in SING_PATTERNS):
            return
        scene = re.sub(r"^(爱弥斯[，, ]*)?", "", text)
        scene = re.sub(r"(看看你|让我看看你|长什么样|你长什么|自拍|拍一张|拍张照|发张照片|发一张照片|来张照片)", "", scene).strip("，, 。.")
        umo = event.unified_msg_origin
        if umo in self._pending_image_tasks:
            return
        # 不 stop_event：DS 自由回复；插件在后台直接补发图片
        self._pending_image_tasks[umo] = asyncio.create_task(self._send_image_later(event, scene))

    @filter.command("爱弥斯外貌")
    async def appearance_cmd(self, event: AstrMessageEvent):
        yield event.plain_result(identity_block(self.store.identity(), compact=False))

    @filter.command("现在在干嘛")
    async def current_activity_cmd(self, event: AstrMessageEvent):
        life = await self.life.read(force=True)
        if not life.get("available"):
            yield event.plain_result("现在的生活状态暂时没读到，等会儿再告诉你～")
            return
        parts = [
            f"现在在{life['activity']}" if life.get("activity") else "",
            f"在{life['location']}" if life.get("location") else "",
            f"心情{life['mood']}" if life.get("mood") else "",
        ]
        yield event.plain_result("，".join(p for p in parts if p) or "现在在忙自己的事。")

    @filter.command("今天穿什么")
    async def outfit_state_cmd(self, event: AstrMessageEvent):
        life = await self.life.read(force=True)
        if not life.get("available"):
            yield event.plain_result("穿搭状态暂时没读到，等会儿再看看～")
            return
        yield event.plain_result(
            life.get("outfit") or "现在穿的是日常居家服，具体细节没记下来。"
        )

    @filter.command("生活状态")
    async def life_state_cmd(self, event: AstrMessageEvent):
        life = await self.life.read(force=True)
        if not life.get("available"):
            yield event.plain_result("现在的生活状态暂时没读到。")
            return
        if hasattr(self.life, "status_block"):
            text = self.life.status_block(life)
        else:
            # 兼容热重载后残留的旧 LifeStateReader
            labels = (
                ("time", "时间"), ("activity", "当前活动"), ("location", "当前地点"),
                ("outfit", "当前穿搭"), ("mood", "当前情绪"), ("energy", "精力"),
                ("weather", "天气"), ("sleep", "睡眠状态"),
            )
            lines = [f"{label}：{life[key]}" for key, label in labels if life.get(key)]
            missing = [
                label for key, label in (("activity", "活动"), ("location", "地点"), ("outfit", "穿搭"))
                if not life.get(key)
            ]
            if missing:
                lines.append("未记录：" + "、".join(missing) + "（生图时由 AI 按当前时间/情绪即兴补足，不写回生活档案）")
            text = "\n".join(lines)
        memory = self.store.get_visual_memory(event.unified_msg_origin) if hasattr(self.store, "get_visual_memory") else {}
        if memory:
            ctx = [
                f"上下文推断（{memory.get('source', 'context_ai')}）：",
                f"活动：{memory['activity']}" if memory.get("activity") else "",
                f"地点：{memory['location']}" if memory.get("location") else "",
                f"穿搭：{memory['outfit']}" if memory.get("outfit") else "",
            ]
            text += "\n" + "\n".join(line for line in ctx if line)
        context = (await self._conversation_context(event.unified_msg_origin)) or self._recent_state_answer(event.unified_msg_origin)
        if context:
            extracted = await self.state_extractor.extract(context, life)
            if extracted:
                if hasattr(self.store, "save_visual_memory"):
                    self.store.save_visual_memory(event.unified_msg_origin, extracted)
                text += "\n对话上下文（DeepSeek）："
                if extracted.get("activity"):
                    text += f"\n活动：{extracted['activity']}"
                if extracted.get("location"):
                    text += f"\n地点：{extracted['location']}"
                if extracted.get("outfit"):
                    text += f"\n穿搭：{extracted['outfit']}"
            else:
                text += "\n最近对话自述：" + context.replace("\n", " ")[:240]
        yield event.plain_result(text)

    @filter.command("换装")
    async def outfit_cmd(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        name = text[2:].strip() if text.startswith("换装") else text.strip()
        if not name:
            yield event.plain_result("用法：/换装 白色连衣裙")
            return
        if self.store.set_outfit(name):
            yield event.plain_result(f"好，换上「{name}」了。")
        else:
            yield event.plain_result(f"还没有「{name}」这套造型，可以先用 /造型列表 看看。")

    @filter.command("造型列表")
    async def outfit_list_cmd(self, event: AstrMessageEvent):
        names = [x["name"] for x in self.store.list_outfits()]
        yield event.plain_result("造型：" + "、".join(names))

    @filter.command("恢复默认")
    async def reset_outfit_cmd(self, event: AstrMessageEvent):
        self.store.set_outfit("官方默认")
        yield event.plain_result("已恢复官方默认造型。")

    @filter.command("视觉状态")
    async def status_cmd(self, event: AstrMessageEvent):
        st = self.store.stats()
        ident = self.store.identity()
        yield event.plain_result(
            f"视觉档案 v{ident.get('version', 1)}，当前造型：{ident.get('variable', {}).get('current_outfit', '官方默认')}；"
            f"素材 {st['assets']} 张，生成 {st['generations']} 次，累计约 ¥{st.get('cost', 0.0):.2f}。"
        )

    @filter.command("视觉成本")
    async def cost_cmd(self, event: AstrMessageEvent):
        st = self.store.stats()
        yield event.plain_result(
            f"生成 {st['generations']} 次，累计约 ¥{st.get('cost', 0.0):.2f}；素材 {st['assets']} 张。"
        )

    @filter.command("视觉恢复")
    async def restore_cmd(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        path_text = text[4:].strip() if text.startswith("视觉恢复") else text.strip()
        if not path_text:
            yield event.plain_result("用法：/视觉恢复 /path/to/visual_backup_xxx.zip")
            return
        try:
            self.store.restore(Path(path_text))
            yield event.plain_result("视觉档案已从备份恢复。")
        except Exception as exc:
            yield event.plain_result(f"恢复失败：{str(exc)[:180]}")

    @filter.command("图片比对")
    async def compare_cmd(self, event: AstrMessageEvent):
        sources = image_sources(event)
        if not sources:
            yield event.plain_result("请带一张图片，再发 /图片比对。")
            return
        try:
            image = await download_image(sources[0], self.data_dir / "images" / "inputs")
            refs = self.store.current_refs()
            report = await self.vision.compare(image, refs)
            yield event.plain_result(
                "像不像：" + json.dumps(report, ensure_ascii=False)[:900]
            )
        except Exception as exc:
            yield event.plain_result(f"比对失败：{str(exc)[:180]}")

    @filter.command("添加造型")
    async def add_outfit_cmd(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        name = text[4:].strip() if text.startswith("添加造型") else text.strip()
        if not name:
            yield event.plain_result("用法：发一张服装图，并发送 /添加造型 白色连衣裙")
            return
        sources = image_sources(event)
        if not sources:
            yield event.plain_result("没有收到服装图片。")
            return
        try:
            image = await download_image(sources[0], self.store.outfits_dir)
            dest = self.store.outfits_dir / (re.sub(r"[^\w\u4e00-\u9fff.-]+", "_", name)[:60] + ".png")
            dest.write_bytes(image.read_bytes())
            try:
                report = await self.vision.analyze_identity(dest)
                desc = json.dumps(report, ensure_ascii=False)[:500]
            except Exception:
                desc = "用户上传造型，未分析"
            self.store.add_outfit(name, desc, [str(dest)])
            yield event.plain_result(f"已保存造型「{name}」，用 /换装 {name} 切换。")
        except Exception as exc:
            yield event.plain_result(f"保存造型失败：{str(exc)[:180]}")

    @filter.command("视觉备份")
    async def backup_cmd(self, event: AstrMessageEvent):
        backup_dir = self.data_dir / "backups"
        backup_dir.mkdir(exist_ok=True)
        target = backup_dir / f"visual_backup_{int(time.time())}.zip"
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as z:
            for f in self.data_dir.rglob("*"):
                if f.is_file() and f != target and "backups" not in f.parts:
                    z.write(f, f.relative_to(self.data_dir).as_posix())
        yield event.plain_result(f"备份完成：{target}（{target.stat().st_size // 1024} KB）")

    @filter.command("视觉校准")
    async def calibrate_cmd(self, event: AstrMessageEvent):
        limit = max(1, min(13, int(self.config.get("calibration_limit", 13))))
        # 只用正视角/面部/全身，避免背面红色打光污染发色
        images = [
            p for p in self.store.official_images()
            if any(k in p.name.lower() for k in ("face", "front", "full"))
        ][:limit]
        if not images:
            yield event.plain_result("没有官方素材。")
            return
        features = {}
        errors = []
        for image in images:
            try:
                result = await self.vision.analyze_identity(image)
                for k, v in result.items():
                    if v and str(v).lower() != "unknown" and k not in features:
                        features[k] = v
            except Exception as exc:
                errors.append(f"{image.name}: {exc}")
        if not features:
            yield event.plain_result("视觉校准失败：" + "；".join(errors[:3]))
            return
        data = self.store.identity()
        fixed = data.setdefault("fixed", {})
        fixed.update(features)
        data["source"] = "vision_calibration_front_only"
        data["confidence"] = 0.95
        self.store.save_identity(data)
        yield event.plain_result(
            f"视觉校准完成，已分析 {len(images)} 张正视角图，更新 {len(features)} 个字段。"
            + (f"\n部分失败：{errors[0]}" if errors else "")
        )

    @filter.command("修正身份")
    async def fix_identity_cmd(self, event: AstrMessageEvent):
        text = (event.message_str or "").strip()
        body = text[4:].strip() if text.startswith("修正身份") else text.strip()
        key_map = {
            "发色": "hair_color", "发型": "hair_style", "刘海": "bangs",
            "侧发": "side_hair", "后发": "back_hair", "发饰": "hair_ornament",
            "瞳色": "eye_color", "眼型": "eye_shape", "脸型": "face_style",
            "服装": "outfit_default",
        }
        data = self.store.identity()
        fixed = data.setdefault("fixed", {})
        changed = []
        for part in re.split(r"[;；,，]\s*", body):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            value = value.strip()
            field = key_map.get(key, key)
            if field and value:
                fixed[field] = value
                changed.append(field)
        if not changed:
            yield event.plain_result(
                "用法：/修正身份 发色=粉色; 发型=粉色长双马尾; 后发=长直发"
            )
            return
        data["source"] = "user_confirmed"
        data["confidence"] = 1.0
        self.store.save_identity(data)
        yield event.plain_result("已修正：" + "、".join(changed))
