# 爱弥斯·视觉身份：安装与配置

## 1. 目录

```text
astrbot_plugin_aemeath_visual_identity/
├── main.py
├── core/
│   ├── db.py
│   ├── imagegen.py
│   ├── image_utils.py
│   ├── prompt.py
│   └── vision.py
├── assets/official/13 张官方图
├── tools/import_official.py
├── _conf_schema.json
├── metadata.yaml
└── README.md
```

## 2. 安装

把整个目录放到：

```text
data/plugins/astrbot_plugin_aemeath_visual_identity
```

重启或在 WebUI 插件页重载。

首次加载会：

1. 复制 `assets/official/` 到 `data/plugin_data/astrbot_plugin_aemeath_visual_identity/images/official/`
2. 计算 SHA256
3. 建立 `identity.db`
4. 写入默认身份和 13 张素材

## 3. 最小配置

只保留两个 API：

```text
vision_provider = mimo

mimo_base_url = https://api.xiaomimimo.com/v1
mimo_api_key  = ...
mimo_model    = mimo-v2.6-flash

gpt_image_base_url = https://qacn.dashun9527.com/v1
gpt_image_api_key  = ...
gpt_image_model    = gpt-image-2
```

qwen 只作为失败兜底，不需要可以关闭：

```text
qwen_image_enabled = false
```

Claude 可选，留空即可：

```text
claude_api_key = ""
```

## 4. 首次建档

1. 确认 13 张官方图在 `assets/official/`
2. 发送 `/视觉状态` 确认插件在线
3. 发送 `/视觉校准`
   - 用 MiMo 逐张分析
   - 更新 `identity.db`
   - 只需要做一次
4. 发送 `/爱弥斯外貌` 检查结果
5. 不满意可重新校准或手动改库

## 5. 生图

```text
/看看你
爱弥斯，今天让我看看你
爱弥斯，去海边拍一张
```

流程：

```text
MiMo 规划场景/姿势/服装
→ gpt-image-2 参考图编辑
→ 失败则 qwen-image-3.0
→ MiMo 质量检查
→ 保存 PNG
→ QQ 发送
```

## 6. 造型

```text
发服装图 + /添加造型 白色连衣裙
/换装 白色连衣裙
/造型列表
/恢复默认
```

## 7. 图片比对

```text
/图片比对（带图）
这张图是不是我（带图）
这个发饰和我的一样吗（带图）
```

## 8. 成本与备份

```text
/视觉成本
/视觉备份
/视觉恢复 /path/to/visual_backup_xxx.zip
```

## 9. 常见问题

### 没反应

检查：

- NapCat 是否运行
- 插件是否打开
- `gpt_image_base_url` 是否可用
- `mimo_api_key` 是否有效

### 502

gpt-image 上游可能临时不可用；插件会自动回退 qwen（若开启）。

### 图片不像

- 提高到 2 个候选：`candidates=2`
- 用 `/视觉校准` 重新建档
- 换更清晰的官方面部图
- 自由姿势本身会漂移，不会每张 100% 一致

### 角色一致性极限

纯 API + 参考图无法保证任意姿势完全同一张脸。  
若必须严格一致，需要 LoRA / 自部署，超出本插件默认能力。
