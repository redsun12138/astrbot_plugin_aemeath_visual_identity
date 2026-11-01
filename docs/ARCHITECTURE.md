# 架构

```text
QQ / AstrBot 事件
        │
        ├─ on_llm_request ──► 身份片段注入
        │
        ├─ /看看你 / 自拍 / 自然语言
        │       │
        │       ├─ MiMo planner：scene / pose / outfit
        │       ├─ 读 SQLite 身份 + 当前造型
        │       ├─ 选参考图：face / full / side / outfit
        │       ├─ gpt-image-2 /images/edits
        │       │       └─ 失败 → qwen-image-3.0
        │       ├─ MiMo 质量检查
        │       ├─ 保存 PNG + 生成记录 + 成本
        │       └─ Comp.Image 发送
        │
        ├─ /图片比对（带图）
        │       └─ Claude/MiMo 比较用户图与官方图
        │
        ├─ /添加造型（带图）
        │       └─ 保存造型图 + 分析 + SQLite
        │
        └─ /视觉备份 / 恢复
```

## 数据表

```text
assets        图片路径、SHA256、来源、类型、确认状态
identity      固定身份 + 可变造型 + 参考图 + 版本
outfits       自定义造型
generations   生图记录、后端、成本、缓存
settings      预留
```

## 优先级

```text
官方确认身份
> 用户确认造型
> 视觉模型分析
> 普通聊天
> 模型推测
```

## 模型分工

```text
gpt-image-2    生图/参考图编辑（主力）
qwen-image-3.0 生图回退（可选）
MiMo 2.6 Flash 看图、比对、规划、质量检查
Claude         可选高精度视觉分析
DeepSeek       日常聊天（不参与视觉）
```

## 缓存

```text
同一 Prompt + 同一参考图列表 → 直接复用 generations.path
```

## 已知限制

- 纯 API 参考图无法保证任意姿势 100% 同一张脸
- 自由换装/姿势会漂移
- 严格一致性需要 LoRA / 自部署
