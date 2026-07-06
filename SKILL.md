---
name: billiards-joke-video
description: "台球厅段子视频技能：自动搜索/接收段子 → 生成3段10秒脚本 → Seedance 2.0 链式续拍生成 → 拼接30秒成品 → 可选配音。触发词：台球厅段子视频、台球段子、每日台球视频、台球城视频。"
version: 1.0.0
category: file-generation
argument-hint: "[段子文本] [--scene-images URL1 URL2] [--person-images URL1 URL2]"
---

# 台球厅段子视频技能

为「天宫国际台球城」制作30秒段子短视频，全自动一条龙。

## 场景名称

**天宫国际台球城** — 所有脚本必须包含此名称，出现在对白或场景描述中。

## 完整工作流

### Step 1: 获取段子

| 输入来源 | 处理方式 |
|---------|---------|
| 用户直接提供段子文本 | 直接使用 |
| 用户未提供 | 用 `web_search` 搜索"台球 段子 热门"/"台球厅 搞笑" 等，筛选 30 秒内能讲完、有反转的段子，由 AI 改写为视频脚本 |

### Step 2: 编写3段脚本

将段子拆分为3段，每段10秒，结构如下：

| 段落 | 时间 | 功能 | 要点 |
|------|------|------|------|
| **段1·钩子** | 0-10s | 制造悬念/冲突 | 开场抓住注意力 |
| **段2·展开** | 10-20s | 冲突升级/铺垫 | 笑点逐步推进 |
| **段3·反转** | 20-30s | 包袱抖出/收尾 | 反转高潮结束 |

**每段脚本必须包含：**

1. **统一人物描述**（防漂移）：从段1开始锁定，段2/3完全复用
   - 示例：`"一位年轻男子，短发，穿白色Polo衫，牛仔裤，身材中等"`
2. **统一场景描述**（防漂移）：每段重复
   - 示例：`"天宫国际台球城内部，暖黄色灯光，多张绿色台球桌，墙上挂着球杆，环境整洁高档"`
3. **本段动作描述**：叙述本段具体表演内容
4. **品牌植入**：`"天宫国际台球城"` 必须出现在场景描述或对白中

**脚本模板（每段 prompt 的构造格式）：**

```
[人物]: {统一人物描述}
[场景]: 天宫国际台球城，{场景细节}
[动作]: {本段具体动作}
[运镜]: {镜头运动描述}
```

### Step 3: 生成视频（链式续拍）

使用 Seedance 2.0 模型，串行生成3段视频，通过尾帧链接保证连贯性。

**模型与参数：**

| 参数 | 值 | 说明 |
|------|-----|------|
| model | `doubao-seedance-2-0-260128` | Seedance 2.0 |
| resolution | `480p` | 竖屏适配 |
| ratio | `9:16` | 竖屏抖音格式 |
| duration | `10` | 每段10秒 |
| return_last_frame | `true` | 全部3段都返回尾帧 |
| generate_audio | `true` | 生成环境音效 |
| service_tier | `default` | 快速模式 |

**生成流程（严格串行）：**

```
段1: text [+ reference_images] → seedance create --wait --download /root/video
     ↓ 取返回的 last_frame_url

段2: text [+ reference_images] + first_frame:last_frame_url → seedance create --wait --download /root/video
     ↓ 取返回的 last_frame_url

段3: text [+ reference_images] + first_frame:last_frame_url → seedance create --wait --download /root/video
```

**参考图处理（可选输入）：**

| 输入参数 | role | 用途 |
|---------|------|------|
| `person_images` | `reference_image` | 锁定人物外貌，3段都传 |
| `scene_images` | `reference_image` | 锁定台球厅环境，3段都传 |

用户不提供参考图时，段1纯文生视频，段2/3依靠尾帧续拍保持连贯。

**调用命令模板：**

```bash
# 段1（无续拍）
export ARK_API_KEY="从环境变量获取"
python3 ~/.openclaw/skills/seedance-video-generation/seedance.py create \
  --prompt "[人物描述] [场景描述含天宫国际台球城] [动作描述]" \
  --model doubao-seedance-2-0-260128 \
  --ratio 9:16 \
  --duration 10 \
  --resolution 480p \
  --return-last-frame true \
  --generate-audio true \
  --wait \
  --download /root/video

# 段2/3（尾帧续拍）— 需要从段1/2的任务结果中取出 last_frame_url
python3 ~/.openclaw/skills/seedance-video-generation/seedance.py create \
  --prompt "[同样的人物+场景描述] [本段动作描述]" \
  --image "{上段的last_frame_url}" \
  --model doubao-seedance-2-0-260128 \
  --ratio 9:16 \
  --duration 10 \
  --resolution 480p \
  --return-last-frame true \
  --generate-audio true \
  --wait \
  --download /root/video
```

**注意：** seedance.py 目前的 `--image` 参数默认 role 为 `first_frame`，正好用来传入尾帧续拍，无需修改。

**当有参考图时，段1命令变为：**

```bash
python3 ~/.openclaw/skills/seedance-video-generation/seedance.py create \
  --prompt "[人物描述] [场景描述含天宫国际台球城] [动作描述]" \
  --ref-images person.jpg scene.jpg \
  --model doubao-seedance-2-0-260128 \
  --ratio 9:16 \
  --duration 10 \
  --resolution 480p \
  --return-last-frame true \
  --generate-audio true \
  --wait \
  --download /root/video
```

**⚠️ 重要：获取尾帧 URL**

seedance.py `--wait` 模式返回完整 JSON，从中提取 `last_frame_url`：

```python
import json
result = json.loads(output)
last_frame_url = result.get("content", {}).get("last_frame_url", "")
```

如果 seedance.py 的 CLI 输出不够结构化，可改用 curl 直接调用 API 并手动解析：

```bash
# 创建任务
TASK_RESULT=$(curl -s -X POST "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer $ARK_API_KEY" \
  -d '{...}')
TASK_ID=$(echo "$TASK_RESULT" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")

# 轮询等待完成
while true; do
  STATUS=$(curl -s -X GET "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks/$TASK_ID" \
    -H "Authorization: Bearer $ARK_API_KEY")
  STATE=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['status'])")
  [ "$STATE" = "succeeded" ] && break
  [ "$STATE" = "failed" ] && echo "FAILED" && break
  sleep 15
done

# 提取视频URL和尾帧URL
VIDEO_URL=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['content']['video_url'])")
LAST_FRAME=$(echo "$STATUS" | python3 -c "import sys,json; print(json.load(sys.stdin)['content'].get('last_frame_url',''))")

# 下载视频
curl -s -o "/root/video/seg{N}.mp4" "$VIDEO_URL"
```

### Step 4: 拼接视频

使用 video-concat 技能的 `concat_video.py`，重编码统一参数确保抖音兼容。

```bash
python3 ~/.openclaw/skills/video-concat/scripts/concat_video.py \
  --inputs /root/video/seg1.mp4 /root/video/seg2.mp4 /root/video/seg3.mp4 \
  --output /root/video/billiards_final_$(date +%Y%m%d_%H%M%S).mp4
```

默认输出 H.264 High Profile Level 4.1 + AAC 44100Hz 立体声 + yuv420p + faststart，完全适配抖音。

### Step 5: 配音（可选）

如果需要旁白+BGM+字幕，使用 video-dubbing-v3：

```bash
python3 ~/.openclaw/skills/video-dubbing-v3/scripts/smart_dub.py \
  --video /root/video/billiards_final_XXXXXX.mp4 \
  --text "{完整30秒旁白文本}" \
  --voice zh_male_taocheng_uranus_bigtts \
  --emotion happy \
  --emotion-scale 4 \
  --bgm-style "upbeat,chill" \
  --bgm-volume 0.15 \
  --output /root/video/billiards_dubbed_XXXXXX.mp4
```

推荐配音参数：

| 参数 | 推荐值 | 理由 |
|------|--------|------|
| voice | `zh_male_taocheng_uranus_bigtts`（小天） | 阳光活力，适合段子 |
| emotion | `happy` | 搞笑段子气氛 |
| emotion-scale | `4` | 适度夸张 |
| bgm-style | `upbeat,chill` | 轻松愉快 |
| bgm-volume | `0.15` | 不压人声 |

## 完整一键脚本

`scripts/run.py` 封装了全流程，可以用一条命令执行：

```bash
python3 ~/.openclaw/workspace_daoyan/skills/billiards-joke-video/scripts/run.py \
  --joke "可选，段子文本" \
  --scene-images "可选，场景图URL" \
  --person-images "可选，人物图URL" \
  --with-dubbing \
  --output /root/video
```

## 防漂移 Checklist

生成前确认以下要点：

- [ ] 3段 prompt 的**人物描述**完全一致（逐字复制）
- [ ] 3段 prompt 的**场景描述**完全一致（逐字复制）
- [ ] 每段 scene 描述都包含 `"天宫国际台球城"`
- [ ] 段2/段3 的 `first_frame` 使用上一段返回的 `last_frame_url`
- [ ] 参考图（如有）在3段中都传入
- [ ] `return_last_frame: true` 在3段中都设置

## 错误处理

| 错误类型 | 处理方式 |
|---------|---------|
| OutputVideoSensitiveContentDetected | 同一 prompt 最多重试1次，若仍失败则改写 prompt 去掉敏感内容再试 |
| 任务超时（>5分钟仍在 running） | 报告用户，询问是否继续等待或跳过 |
| 任务失败（failed） | 检查错误信息，修正参数后重试1次 |
| 下载失败 | 重试3次，间隔10秒 |

## 成本参考

| 项目 | 估算 |
|------|------|
| 段1（纯文生） | ~50k tokens ≈ 2.3元 |
| 段2（尾帧续拍） | ~100k tokens ≈ 4.6元 |
| 段3（尾帧续拍） | ~100k tokens ≈ 4.6元 |
| 配音（可选） | ~0.5元 |
| **单次总计** | **~12元** |
| **月成本（每天1条）** | **~360元** |
| flex 模式月成本 | **~180元** |
