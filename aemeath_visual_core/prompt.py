"""身份描述与生图 Prompt 构造。"""
from __future__ import annotations

from typing import Any


def identity_lines(data: dict[str, Any]) -> list[str]:
    fixed = data.get("fixed", {})
    return [
        f"发色：{fixed.get('hair_color', 'unknown')}",
        f"发型：{fixed.get('hair_style', 'unknown')}",
        f"刘海：{fixed.get('bangs', 'unknown')}",
        f"侧发：{fixed.get('side_hair', 'unknown')}",
        f"后发：{fixed.get('back_hair', 'unknown')}",
        f"发饰：{fixed.get('hair_ornament', 'unknown')}",
        f"瞳色：{fixed.get('eye_color', 'unknown')}",
        f"眼型：{fixed.get('eye_shape', 'unknown')}",
        f"脸型：{fixed.get('face_style', 'unknown')}",
        f"默认服装：{fixed.get('outfit_default', 'unknown')}",
    ]


def identity_block(data: dict[str, Any], compact: bool = True) -> str:
    fixed = data.get("fixed", {})
    current = data.get("variable", {}).get("current_outfit", "官方默认")
    if compact:
        return (
            "【视觉身份】你是爱弥斯。固定外貌：粉色长发双马尾、分层斜刘海、"
            "蓝银色羽翼/星形发饰、琥珀金色星瞳、柔和圆脸、官方二次元画风。"
            f"当前造型：{current}。不要把自己描述成真人、cosplay 或相似角色。"
        )
    return "【视觉身份】\n" + "\n".join(identity_lines(data)) + f"\n当前造型：{current}"


def build_generation_prompt(
    identity: dict[str, Any],
    scene: str = "",
    pose: str = "",
    outfit: str = "",
    extra: str = "",
    life_state: dict[str, Any] | None = None,
) -> str:
    fixed = identity.get("fixed", {})
    life = life_state if isinstance(life_state, dict) else {}
    current = outfit or life.get("outfit") or identity.get("variable", {}).get("current_outfit", "官方默认")
    if not scene:
        scene = life.get("location") or life.get("scene") or "室内窗边，柔和自然光"
    if not pose:
        pose = "根据当前生活状态自然呈现动作，不要固定站立"
    life_lines = []
    for key, label in (
        ("time", "时间"), ("activity", "当前活动"), ("location", "当前地点"),
        ("outfit", "当前穿搭"), ("mood", "当前情绪"), ("energy", "精力"),
        ("weather", "天气"), ("sleep", "睡眠状态"),
    ):
        if life.get(key):
            life_lines.append(f"{label}：{life[key]}")
    life_block = "\n".join(life_lines)
    deepseek_context = str(life.get("deepseek_context") or "").strip()
    context_block = (
        "\n【最近对话中角色的自述（DeepSeek）】\n" + deepseek_context
        + "\n如果这段自述提到当前活动、地点或穿搭，以这段为准，不要使用官方默认舞台礼服。"
        if deepseek_context else ""
    )
    return (
        "以参考图中的角色为唯一身份来源，严格保持她的固定视觉身份："
        f"发色={fixed.get('hair_color', '粉色')}；"
        f"发型={fixed.get('hair_style', '长发双马尾')}；"
        f"刘海={fixed.get('bangs', '分层斜刘海')}；"
        f"发饰={fixed.get('hair_ornament', '蓝银色羽翼发饰')}；"
        f"瞳色={fixed.get('eye_color', '琥珀金色星瞳')}；"
        f"脸型={fixed.get('face_style', '柔和少女脸')}。"
        "保持官方二次元插画风格，不要真人化、不要cosplay、不要换脸。"
        "不要复制参考图的姿势；可以自由改变姿势、镜头和场景。"
        + (f"\n【当前生活状态】\n{life_block}\n除非用户明确要求改变，否则不要改变这些已记录的生活事实。" if life_block else "")
        + context_block
        + f"\n当前服装：{current}。场景：{scene}。姿势：{pose}。"
        f"{extra}"
        "画面是当下的生活抓拍瞬间，不是标准立绘；自然光照，真实生活细节。"
        "高细节，干净构图，无文字，无水印，无多余手指。"
    )
