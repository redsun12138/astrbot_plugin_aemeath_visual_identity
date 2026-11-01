# 爱弥斯·视觉身份

独立于 `content_companion` 的视觉身份与自主生图插件。

## 功能

- 官方参考图导入、SHA256、SQLite 档案
- 固定身份 / 可变造型分离
- MiMo / Claude 视觉分析、图片比对、质量检查
- AI 自动规划场景 / 姿势 / 服装
- `gpt-image-2` 参考图编辑优先，`qwen-image-3.0` 自动回退
- 自然语言“让我看看你”生图
- 自定义造型上传与切换
- QQ 图片发送
- 生成缓存、成本统计、备份/恢复
- 读取 `astrbot_plugin_private_companion` 的生活状态，生成当前生活瞬间

## 生活状态驱动

生图前调用 `private_companion` 的现有接口：

```python
plugin = context.get_registered_star("astrbot_plugin_private_companion")
scene = plugin._build_companion_scene_snapshot()
```

读取：

```text
当前活动 / 地点 / 穿搭 / 情绪 / 精力 / 天气 / 睡眠 / 时间
```

规则：

- 已记录状态优先，不覆盖成随机场景
- 用户明确换装/换地点时才改变
- 读取失败时降级为固定身份生图，不影响聊天
- 普通聊天不生成图片；`你现在在干嘛`、`今天穿什么` 只回答状态

## 命令

```text
/看看你
/自拍
/爱弥斯外貌
/视觉校准
/修正身份 发色=粉色; 发型=粉色长双马尾
/换装 官方默认
/添加造型 白色连衣裙
/造型列表
/恢复默认
/图片比对
/视觉状态
/视觉成本
/视觉备份
/视觉恢复 /path/to/backup.zip
```

自然语言：

```text
爱弥斯，今天让我看看你
爱弥斯，去海边拍一张
爱弥斯，你长什么样
这张图是不是我
```

## 最小配置

```text
vision_provider = mimo

mimo_base_url = https://api.xiaomimimo.com/v1
mimo_api_key  = ...
mimo_model    = mimo-v2.6-flash

gpt_image_base_url = https://qacn.dashun9527.com/v1
gpt_image_api_key  = ...
gpt_image_model    = gpt-image-2
image_size         = 1024x1536
```

qwen 只作失败兜底；不需要可关闭：

```text
qwen_image_enabled = false
```

Claude 可选，留空即不调用：

```text
claude_api_key = ""
```

## 数据

```text
data/plugin_data/astrbot_plugin_aemeath_visual_identity/
├── identity.db
├── images/official/
├── images/outfits/
├── images/generated/
└── backups/
```

第一次加载会把插件 `assets/official/` 的 13 张官方图复制到 `images/official/`，并建立 SQLite 档案。

## 视觉校准

```text
/视觉校准
```

只使用 `face / front / full` 正视角图，避免背面红色打光污染发色。

如果校准仍然污染字段，用用户确认覆盖：

```text
/修正身份 发色=粉色/浅粉色; 发型=粉色长双马尾; 后发=长直发; 发饰=蓝银色羽翼/星形发饰; 瞳色=琥珀金/橙色
```

## 说明

- 日常生图只读本地档案和参考图，不重复分析官方图。
- `gpt-image-2` 支持参考图编辑；失败会回退 qwen。
- 角色一致性受模型能力限制；自由姿势/换装可能漂移。
- 图片比对是语义比较，不是人脸身份鉴定。
