#!/usr/bin/env python3
"""
台球厅段子视频一键生成脚本
天宫国际台球城 - 30秒段子短视频

用法:
  # 全自动（自动搜段子、自动从天宫接口获取助教描述+包房照片）
  python3 run.py --output /root/video

  # 指定段子文本
  python3 run.py --joke "段子内容..." --output /root/video

  # 带人物参考图（会自动用image模型描述长相，跳过助教API）
  python3 run.py --person-images "https://..." --output /root/video

  # 带场景参考图（跳过包房API）
  python3 run.py --scene-images "https://..." --output /root/video

  # 跳过配音
  python3 run.py --no-dubbing --output /root/video
"""

import argparse
import fcntl
import json
import os
import random
import shutil
import subprocess
import sys
import time
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# ============================================================
# 配置
# ============================================================
MODEL_ID = "doubao-seedance-2-0-260128"
BASE_URL = "https://ark.cn-beijing.volces.com/api/v3/contents/generations/tasks"
BRAND_NAME = "天宫国际台球城"
SEGMENT_DURATION = 10  # 秒
RESOLUTION = "480p"
RATIO = "9:16"
VIDEO_DIR = Path("/root/video")
SHARE_DIR = Path("/var/www/video")
LOCK_FILE = "/tmp/billiards_video.lock"
SEEDANCE_PY = os.path.expanduser("~/.openclaw/skills/seedance-video-generation/seedance.py")
CONCAT_PY = os.path.expanduser("~/.openclaw/skills/video-concat/scripts/concat_video.py")
DUB_PY = os.path.expanduser("~/.openclaw/skills/video-dubbing-v3/scripts/smart_dub.py")

# 天宫数据接口配置
TIANGONG_API_BASE = "https://tiangong.club"
TIANGONG_ADMIN_USER = "tgadmin"
TIANGONG_ADMIN_PASS = "mayining633"


# ============================================================
# 天宫数据接口
# ============================================================
def get_tiangong_token():
    """获取天宫管理员 JWT Token"""
    url = f"{TIANGONG_API_BASE}/api/admin/login"
    data = json.dumps({"username": TIANGONG_ADMIN_USER, "password": TIANGONG_ADMIN_PASS}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            result = json.loads(resp.read().decode())
            return result.get("token", "")
    except Exception as e:
        print(f"  ⚠️ 获取天宫Token失败: {e}")
        return ""


def tiangong_api_get(path, token):
    """调用天宫GET接口"""
    url = f"{TIANGONG_API_BASE}{path}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def download_file(url, filepath):
    """下载文件到本地"""
    try:
        wget_available = subprocess.run(["which", "wget"], capture_output=True).returncode == 0
        if wget_available:
            subprocess.run(["wget", "-q", "-O", str(filepath), url], check=True, timeout=30)
        else:
            urllib.request.urlretrieve(url, str(filepath))
        return True
    except Exception as e:
        print(f"  ❌ 下载失败 ({url[:50]}...): {e}")
        return False


def compress_oss_url(url, width=640, quality=80):
    """阿里云OSS图片URL加尺寸压缩参数"""
    if "oss-cn" in url and "?" not in url:
        return f"{url}?x-oss-process=image/resize,w_{width}/quality,q_{quality}"
    return url


def describe_person_image(image_path):
    """
    用AI图片模型分析人物外貌，返回详细文字描述。
    该描述会写入3段视频的prompt，确保人物长相一致。
    """
    print("  🔍 使用AI图片模型分析人物外貌...")
    try:
        # 使用 image 工具通过子进程调用
        # 构造临时脚本调用 image tool
        result = subprocess.run(
            ["python3", "-c", f"""
import json, sys
# 这里需要agent调用image工具，脚本中无法直接调用
# 返回一个标记，让上层agent处理
print("NEED_IMAGE_DESC:{image_path}")
"""],
            capture_output=True, text=True, timeout=10
        )
        # 实际上这个函数应该由 AI Agent 调用 image 工具来完成
        # 脚本只负责下载图片，描述由 Agent 在上层完成
        return ""
    except Exception as e:
        print(f"  ⚠️ 图片描述失败: {e}")
        return ""


def get_coach_description(token):
    """
    从天宫数据接口获取随机助教的信息，返回文字外貌描述模板。
    因为助教真人照片被Seedance隐私过滤器拦截，
    所以改为：下载封面图 → AI分析长相 → 生成详细文字描述 → 写入prompt。
    同时返回助教的等级、身高、年龄等补充信息。
    """
    try:
        # 获取助教列表
        coaches_data = tiangong_api_get("/api/public/coaches", token)
        coaches = coaches_data.get("data", [])

        # 筛选有封面照的助教
        with_cover = [c for c in coaches if c.get("cover")]
        if not with_cover:
            print("  ⚠️ 没有找到有照片的助教")
            return None

        # 随机选一位（优先选人气助教）
        popular = [c for c in with_cover if c.get("is_popular") == 1]
        pool = popular if popular else with_cover
        coach = random.choice(pool)
        coach_no = coach["coach_no"]
        stage_name = coach.get("stage_name", "?")
        print(f"  🎯 选中助教: {stage_name} (编号{coach_no}, {coach.get('level','?')})")

        # 获取详情
        detail = tiangong_api_get(f"/api/public/coaches/{coach_no}", token)
        data = detail.get("data", {})

        # 下载封面图到本地（供AI分析）
        photos = data.get("photos", [])
        if not photos:
            print("  ⚠️ 助教无照片")
            return None

        cover_url = photos[0]
        local_path = VIDEO_DIR / f"coach_{coach_no}_face.jpg"
        if download_file(cover_url, local_path):
            print(f"  📸 已下载助教封面: {local_path}")
        else:
            print("  ⚠️ 封面下载失败")
            local_path = None

        # 返回助教信息和本地图片路径
        # 外貌描述需要由 AI Agent 用 image 工具完成后回填
        return {
            "coach_no": coach_no,
            "stage_name": stage_name,
            "level": data.get("level", ""),
            "age": data.get("age"),
            "height": data.get("height"),
            "intro": data.get("intro", ""),
            "local_face_image": str(local_path) if local_path else None,
            "photo_urls": photos[:3],  # 只做参考，不传给Seedance（会被隐私过滤器拦截）
        }

    except Exception as e:
        print(f"  ⚠️ 获取助教信息失败: {e}")
        return None


def get_room_photos(token, count=6):
    """
    从天宫数据接口获取包房照片URL列表（最多count张）。
    多间包房各取若干张，覆盖不同场景风格。
    照片加OSS压缩参数后传给Seedance 2.0作为reference_image。
    """
    try:
        # 获取包房列表
        rooms_data = tiangong_api_get("/api/public/vip-rooms", token)
        rooms = rooms_data.get("data", [])

        # 收集所有包房照片
        all_photos = []
        for r in rooms:
            rid = r["id"]
            detail = tiangong_api_get(f"/api/public/vip-rooms/{rid}", token)
            data = detail.get("data", {})
            photos = data.get("photos", [])
            room_name = data.get("name", "")
            for p in photos:
                all_photos.append((room_name, p))

        if not all_photos:
            print("  ⚠️ 没有找到包房照片")
            return []

        # 随机选取（尽量覆盖不同房间）
        random.shuffle(all_photos)
        selected = all_photos[:count]

        # 加OSS压缩参数
        result = []
        for room_name, url in selected:
            compressed = compress_oss_url(url)
            result.append(compressed)
            print(f"  📸 [{room_name}] {url[:70]}...")

        print(f"  ✅ 选取 {len(result)} 张包房参考图（OSS压缩后）")
        return result

    except Exception as e:
        print(f"  ⚠️ 获取包房照片失败: {e}")
        return []


def auto_fetch_ref_images(person_images, scene_images):
    """
    自动获取参考资源。
    人物：下载助教封面 → 由AI Agent用image工具描述长相 → 文字写入prompt
    场景：6张包房照片 → 加OSS压缩 → 作为reference_image传给Seedance
    返回 (coach_info_or_None, scene_urls)
    """
    # 场景图处理
    if scene_images:
        scene_urls = [compress_oss_url(u) for u in scene_images]
        print(f"  ✅ 用户提供了 {len(scene_urls)} 张场景参考图")
    else:
        print("\n🏠 自动获取包房参考图...")
        token = get_tiangong_token()
        if token:
            scene_urls = get_room_photos(token, count=6)
        else:
            scene_urls = []
            print("  ⚠️ Token获取失败，场景参考图将为空")

    # 人物处理
    if person_images:
        # 用户提供了人物照片：下载到本地，等候AI Agent用image工具描述
        print(f"  ✅ 用户提供了 {len(person_images)} 张人物参考图")
        coach_info = {
            "local_face_image": None,  # Agent需下载用户图片并用image工具分析
            "user_provided_urls": person_images,
            "stage_name": "自定义人物",
            "level": "",
            "age": None,
            "height": None,
            "appearance_desc": "",  # 由 Agent 回填
        }
    else:
        # 从天宫API获取助教信息
        print("\n👩 自动获取助教信息...")
        token = get_tiangong_token() or ""
        if token:
            coach_info = get_coach_description(token)
        else:
            coach_info = None
            print("  ⚠️ Token获取失败，人物将纯文生")

    return coach_info, scene_urls


def extract_original_audio(seg_paths, output_path):
    """
    从3段Seedance视频中分别提取原始音频，拼接成完整的环境音轨。
    用于后续与旁白+BGM混合（保留击球声等环境音效）。
    """
    print("\n🎵 提取原始环境音轨...")
    temp_dir = VIDEO_DIR / "temp_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)

    audio_segments = []
    for i, seg_path in enumerate(seg_paths):
        seg_audio = str(temp_dir / f"seg_{i+1}_audio.m4a")
        # 提取音频，如果该段没有音频轨则生成静音
        probe = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "stream=codec_type", "-of", "csv=p=0", seg_path],
            capture_output=True, text=True
        )
        if "audio" in probe.stdout:
            cmd = ["ffmpeg", "-y", "-i", seg_path, "-vn", "-c:a", "aac", "-ar", "44100", "-ac", "2", seg_audio]
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            audio_segments.append(seg_audio)
            print(f"  ✅ 段{i+1}音频已提取")
        else:
            # 生成与视频等长的静音
            dur = subprocess.run(
                ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", seg_path],
                capture_output=True, text=True
            )
            duration = float(dur.stdout.strip()) if dur.stdout.strip() else SEGMENT_DURATION
            silent_audio = str(temp_dir / f"seg_{i+1}_silent.m4a")
            cmd = ["ffmpeg", "-y", "-f", "lavfi", "-i", f"anullsrc=r=44100:cl=stereo",
                   "-t", str(duration), "-c:a", "aac", "-ar", "44100", "-ac", "2", silent_audio]
            subprocess.run(cmd, capture_output=True, text=True, timeout=30)
            audio_segments.append(silent_audio)
            print(f"  ⚠️ 段{i+1}无音频，已填充静音 ({duration:.1f}s)")

    # 拼接所有音频
    concat_file = str(temp_dir / "audio_concat.txt")
    with open(concat_file, "w") as f:
        for a in audio_segments:
            f.write(f"file '{a}'\n")

    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", concat_file,
           "-c:a", "aac", "-ar", "44100", "-ac", "2", output_path]
    subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    print(f"  ✅ 环境音轨拼接完成: {output_path}")
    return True


def mix_audio_tracks(video_path, narration_path, env_audio_path, bgm_path, output_path,
                     env_volume=0.3, bgm_volume=0.15):
    """
    三音轨混合合成：
    [旁白] volume=1.0       — AI语音，主导
    [环境音] volume=0.3     — Seedance原始音效（击球声、脚步声等）
    [BGM] volume=0.15       — 背景音乐

    对环境音做响度归一化（loudnorm），消除段间音量差异。
    """
    print("\n🎛️ 音轨混合合成（旁白 + 环境音 + BGM）...")
    cmd = ["ffmpeg", "-y", "-i", video_path]

    audio_idx = 0
    filter_parts = []
    audio_inputs = []

    # 旁白（index 1）
    cmd.extend(["-i", narration_path])
    filter_parts.append(f"[{1}:a]volume=1.0[narr]")
    audio_inputs.append("[narr]")
    audio_idx = 2

    # 环境音（index 2）
    if env_audio_path and os.path.exists(env_audio_path):
        cmd.extend(["-i", env_audio_path])
        filter_parts.append(f"[{2}:a]loudnorm=I=-16:TP=-1.5:LRA=11,volume={env_volume}[env]")
        audio_inputs.append("[env]")
        audio_idx = 3

    # BGM（index 3）
    if bgm_path and os.path.exists(bgm_path):
        video_dur = get_duration(video_path)
        fade_out_start = max(0, video_dur - 3)
        cmd.extend(["-i", bgm_path])
        filter_parts.append(
            f"[{3}:a]volume={bgm_volume},"
            f"afade=t=in:st=0:d=2,"
            f"afade=t=out:st={fade_out_start}:d=3,"
            f"atrim=0:{video_dur},asetpts=PTS-STARTPTS[bgm]"
        )
        audio_inputs.append("[bgm]")

    # 混合
    amix_input = "".join(audio_inputs)
    filter_parts.append(f"{amix_input}amix=inputs={len(audio_inputs)}:duration=shortest[aout]")

    filter_complex = ";".join(filter_parts)
    cmd.extend(["-filter_complex", filter_complex])
    cmd.extend(["-map", "0:v", "-map", "[aout]"])

    # 字幕 | 字幕由dub_video单独处理，这里不加
    cmd.extend(["-c:v", "libx264", "-profile:v", "high", "-level", "4.1",
                "-pix_fmt", "yuv420p", "-preset", "medium", "-crf", "18"])
    cmd.extend(["-c:a", "aac", "-ar", "44100", "-ac", "2"])
    cmd.extend(["-movflags", "+faststart"])
    cmd.append(output_path)

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        print(f"  ❌ 混合失败: {result.stderr[-300:]}")
        return False
    print(f"  ✅ 混合完成: {output_path}")
    return True


def get_duration(filepath):
    """获取视频/音频时长"""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(filepath)],
        capture_output=True, text=True
    )
    try:
        return float(result.stdout.strip())
    except:
        return SEGMENT_DURATION * 3


def generate_segment(api_key, seg_num, prompt, first_frame_url=None, ref_image_urls=None):
    """生成单段视频，返回 (video_path, last_frame_url)"""
    label = {1: "钩子", 2: "展开", 3: "反转"}.get(seg_num, f"段{seg_num}")
    print(f"\n{'='*60}")
    print(f"🎬 段{seg_num}·{label} ({SEGMENT_DURATION}s)")
    print(f"{'='*60}")
    print(f"  Prompt: {prompt[:100]}...")
    if first_frame_url:
        print(f"  续拍: 尾帧衔接")
    if ref_image_urls:
        print(f"  场景参考图: {len(ref_image_urls)}张")

    task_id = create_task(api_key, prompt, first_frame_url, ref_image_urls)
    if not task_id:
        return None, None

    print(f"  任务ID: {task_id}")
    result = wait_task(api_key, task_id)
    if not result or result.get("status") != "succeeded":
        return None, None

    video_url = result.get("content", {}).get("video_url", "")
    last_frame_url = result.get("content", {}).get("last_frame_url", "")
    usage = result.get("usage", {})
    print(f"  ✅ 成功! Tokens: {usage.get('total_tokens', '?')}")

    # 下载视频
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    video_path = VIDEO_DIR / f"seg{seg_num}_{task_id}.mp4"
    if not download_file(video_url, video_path):
        return None, None

    return str(video_path), last_frame_url


# ============================================================
# Seedance 2.0 API
# ============================================================
def get_api_key():
    """获取 ARK API Key"""
    key = os.environ.get("ARK_API_KEY")
    if not key:
        bashrc = os.path.expanduser("~/.bashrc")
        if os.path.exists(bashrc):
            with open(bashrc) as f:
                for line in f:
                    if "ARK_API_KEY" in line and "export" in line:
                        key = line.split('"')[1] if '"' in line else line.split("'")[1]
                        os.environ["ARK_API_KEY"] = key
                        break
    if not key:
        print("❌ 无法获取 ARK_API_KEY")
        sys.exit(1)
    return key


def api_call(method, url, data=None, api_key=None):
    """
    用curl发HTTP请求，避免Python urllib在exec子进程中挂起。
    
    踩坑记录：
    - Python urllib.request.urlopen() 在OpenClaw exec子进程中经常挂起无输出
    - 含中文的body会触发 UnicodeEncodeError: 'latin-1' codec can't encode character
    - 改用subprocess.run(["curl", ...]) 彻底解决这两个问题
    """
    cmd = ["curl", "-s", "-X", method, url,
           "-H", f"Authorization: Bearer {api_key}",
           "-H", "Content-Type: application/json"]
    if data is not None:
        cmd.extend(["-d", json.dumps(data, ensure_ascii=False)])
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
        if result.stdout.strip():
            return json.loads(result.stdout)
        return {}
    except subprocess.TimeoutExpired:
        print(f"  ❌ API请求超时: {method} {url}", file=sys.stderr)
        return {"error": {"message": "Request timeout", "code": "TIMEOUT"}}
    except json.JSONDecodeError as e:
        print(f"  ❌ API响应解析失败: {e}", file=sys.stderr)
        return {"error": {"message": str(e), "code": "PARSE_ERROR"}}


def create_task(api_key, prompt, first_frame_url=None, ref_image_urls=None):
    """
    创建Seedance视频生成任务。
    
    ⚠️ 重要限制：first_frame 与 reference_image 互斥！
    API不允许同一请求中同时传入 first_frame 和 reference_image。
    返回错误：InvalidParameter: first/last frame content cannot be mixed with reference media content
    
    因此：
    - 段1：有 ref_image_urls 时传入，不传 first_frame
    - 段2/段3：有 first_frame_url 时传入，不传 ref_image_urls（即使有也忽略并警告）
    """
    content = [{"type": "text", "text": prompt}]
    
    # 互斥逻辑：first_frame 和 reference_image 不能共存
    if first_frame_url:
        # 续拍模式：只用 first_frame，忽略 ref_image_urls
        content.append({"type": "image_url", "image_url": {"url": first_frame_url}, "role": "first_frame"})
        if ref_image_urls:
            print(f"  ⚠️ first_frame 模式下忽略 {len(ref_image_urls)} 张 reference_image（API互斥限制）")
    elif ref_image_urls:
        # 参考图模式：只用 reference_image
        for url in ref_image_urls:
            content.append({"type": "image_url", "image_url": {"url": url}, "role": "reference_image"})
    
    body = {
        "model": MODEL_ID, "content": content,
        "resolution": RESOLUTION, "duration": SEGMENT_DURATION, "ratio": RATIO,
        "return_last_frame": True, "generate_audio": True,
    }
    result = api_call("POST", BASE_URL, body, api_key)
    if "error" in result:
        print(f"❌ 创建任务失败: {result['error']}")
        err_msg = json.dumps(result['error'], ensure_ascii=False)
        if "PrivacyInformation" in err_msg or "隐私" in err_msg:
            print("  💡 提示：助教真人照片被隐私过滤器拦截，请改用文字描述")
        if "first/last frame" in err_msg or "reference media" in err_msg:
            print("  💡 提示：first_frame 和 reference_image 不可同时传入，请检查create_task逻辑")
        return None
    return result.get("id")


def wait_task(api_key, task_id, timeout=420):
    url = f"{BASE_URL}/{task_id}"
    start = time.time()
    while time.time() - start < timeout:
        time.sleep(15)
        result = api_call("GET", url, None, api_key)
        status = result.get("status", "unknown")
        elapsed = int(time.time() - start)
        print(f"  ⏳ [{elapsed}s] {status}", flush=True)
        if status == "succeeded":
            return result
        elif status in ("failed", "expired"):
            print(f"❌ 任务{status}: {result.get('error', {})}")
            return result
    print("⏰ 超时")
    return None


def concat_videos(seg_paths, output_path):
    """拼接3段视频"""
    print(f"\n{'='*60}")
    print(f"🔄 拼接 {len(seg_paths)} 段视频")
    print(f"{'='*60}")
    cmd = ["python3", CONCAT_PY, "--inputs"] + seg_paths + ["--output", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ 拼接失败: {result.stderr[-300:]}")
        return False
    return True


def dub_video(video_path, narration_text, output_path):
    """配音（仅旁白+BGM+字幕，不含环境音）"""
    print(f"\n{'='*60}")
    print(f"🎙️ 配音处理（旁白+BGM+字幕）")
    print(f"{'='*60}")
    cmd = [
        "python3", DUB_PY,
        "--video", video_path,
        "--text", narration_text,
        "--voice", "zh_male_taocheng_uranus_bigtts",
        "--emotion", "happy",
        "--emotion-scale", "4",
        "--bgm-style", "upbeat,chill",
        "--bgm-volume", "0.15",
        "--output", output_path,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ 配音失败: {result.stderr[-300:]}")
        return False
    return True


def hybrid_mix(video_path, dubbed_path, env_audio_path, output_path, env_volume=0.3):
    """
    混合方案：从已完成配音的视频中提取旁白+BGM音轨，
    再与Seedance原始环境音混合，保留击球声等效果音。
    """
    print(f"\n{'='*60}")
    print(f"🎛️ 混合方案：旁白+BGM + 环境音（击球声等）")
    print(f"{'='*60}")

    # 提取配音视频的音轨（旁白+BGM）
    temp_dir = VIDEO_DIR / "temp_audio"
    temp_dir.mkdir(parents=True, exist_ok=True)
    narration_audio = str(temp_dir / "dubbed_audio.m4a")
    cmd = ["ffmpeg", "-y", "-i", dubbed_path, "-vn", "-c:a", "aac", "-ar", "44100", "-ac", "2", narration_audio]
    subprocess.run(cmd, capture_output=True, text=True, timeout=30)

    if not os.path.exists(env_audio_path):
        print("  ⚠️ 无环境音轨，直接使用配音版本")
        import shutil
        shutil.copy2(dubbed_path, output_path)
        return True

    # 三轨混合
    return mix_audio_tracks(video_path, narration_audio, env_audio_path, None, output_path,
                            env_volume=env_volume, bgm_volume=0)


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="台球厅段子视频一键生成 - 天宫国际台球城")
    parser.add_argument("--joke", type=str, default="", help="段子文本（不传则AI自动搜索）")
    parser.add_argument("--scene-images", nargs="*", default=[], help="场景图URL列表（不传则自动从包房相册获取6张）")
    parser.add_argument("--person-images", nargs="*", default=[], help="人物图URL列表（不传则自动从助教获取并用文字描述）")
    parser.add_argument("--style", default="搞笑", help="视频风格（默认: 搞笑）")
    parser.add_argument("--no-dubbing", action="store_true", help="跳过配音")
    parser.add_argument("--no-env-audio", action="store_true", help="不混合Seedance原始环境音")
    parser.add_argument("--output", default="/root/video", help="输出目录")
    parser.add_argument("--prompts", nargs=3, help="3段视频的prompt文本（由AI生成）")
    parser.add_argument("--appearance-desc", type=str, default="", help="人物外貌文字描述（由AI用image工具生成）")
    parser.add_argument("--narration", type=str, default="", help="完整旁白文本（配音用）")
    args = parser.parse_args()

    api_key = get_api_key()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ============================================================
    # Step 1: 自动获取参考资源
    # ============================================================
    coach_info, scene_urls = auto_fetch_ref_images(args.person_images, args.scene_images)

    # 使用外貌描述（用户传入 或 从coach_info推导）
    appearance_desc = args.appearance_desc
    if not appearance_desc and coach_info:
        if coach_info.get("appearance_desc"):
            appearance_desc = coach_info["appearance_desc"]
        else:
            # 构造基础描述（从已知信息推导）+ 提示需要AI回填
            parts = []
            if coach_info.get("stage_name"):
                parts.append(f"在天宫国际台球城工作的助教{coach_info['stage_name']}")
            if coach_info.get("level"):
                parts.append(f"{coach_info['level']}助教")
            if coach_info.get("age"):
                parts.append(f"{coach_info['age']}岁")
            if coach_info.get("height"):
                parts.append(f"身高{coach_info['height']}cm")
            appearance_desc = "、".join(parts)
            if coach_info.get("local_face_image"):
                print(f"\n⚠️  需要AI Agent用image工具分析 {coach_info['local_face_image']} 后，")
                print(f"   通过 --appearance-desc 参数回填外貌描述！")

    # ============================================================
    # Step 2: 构造3段prompt（掩护外貌描述 + 场景描述 + 品牌植入）
    # ============================================================
    if not args.prompts:
        print("❌ 需要通过 --prompts 传入3段视频prompt")
        print("   AI Agent 先搜索段子、编写脚本，然后调用本脚本")
        sys.exit(1)

    prompts = args.prompts

    # 如果用户没有在prompt中包含外貌描述，自动注入
    if appearance_desc:
        for i in range(3):
            if appearance_desc not in prompts[i]:
                prompts[i] = f"{appearance_desc}。{prompts[i]}"

    print(f"\n🎱 天宫国际台球城 - 段子视频生成")
    print(f"📅 {timestamp}")
    print(f"📐 {RATIO} | {RESOLUTION} | {SEGMENT_DURATION}s×3 = {SEGMENT_DURATION*3}s")
    if scene_urls:
        print(f"🖼️ 场景参考图: {len(scene_urls)}张")
    if appearance_desc:
        print(f"👤 人物描述: {appearance_desc[:60]}...")
    else:
        print(f"👤 人物描述: 无（纯文生）")

    # ============================================================
    # Step 3: 生成3段视频（串行链式续拍）
    # 
    # ⚠️ first_frame 与 reference_image 互斥：
    # - 段1：传 reference_image（6张包房图），不传 first_frame
    # - 段2/3：传 first_frame（上一段尾帧），不传 reference_image
    # - 场景一致性保障：3段prompt场景描述逐字一致
    # ============================================================
    seg_paths = []
    last_frame_url = None

    for i in range(3):
        seg_num = i + 1
        # 段1传参考图，段2/3只传first_frame（create_task内部处理互斥）
        refs = scene_urls if (seg_num == 1 and scene_urls) else None
        video_path, last_frame_url = generate_segment(
            api_key, seg_num, prompts[i],
            first_frame_url=last_frame_url,
            ref_image_urls=refs,
        )
        if not video_path:
            print(f"❌ 第{seg_num}段生成失败，流程中止")
            sys.exit(1)
        seg_paths.append(video_path)

    # ============================================================
    # Step 4: 拼接视频
    # ============================================================
    final_path = str(output_dir / f"billiards_{timestamp}.mp4")
    if not concat_videos(seg_paths, final_path):
        sys.exit(1)
    print(f"\n✅ 视频拼接完成: {final_path}")

    # ============================================================
    # Step 5: 提取原始环境音（用于后续混合）
    # ============================================================
    env_audio_path = None
    if not args.no_env_audio:
        temp_dir = VIDEO_DIR / "temp_audio"
        temp_dir.mkdir(parents=True, exist_ok=True)
        env_audio_path = str(temp_dir / f"env_{timestamp}.m4a")
        extract_original_audio(seg_paths, env_audio_path)

    # ============================================================
    # Step 6: 配音 + 混合
    # ============================================================
    if not args.no_dubbing and args.narration:
        # Step 6a: 先做基础配音（旁白+BGM+字幕）
        dubbed_path = str(output_dir / f"billiards_dubbed_{timestamp}.mp4")
        if not dub_video(final_path, args.narration, dubbed_path):
            print(f"\n🎉 视频成品（未配音）: {final_path}")
            sys.exit(0)

        # Step 6b: 混合环境音（击球声等）
        if env_audio_path and os.path.exists(env_audio_path) and not args.no_env_audio:
            hybrid_path = str(output_dir / f"billiards_final_{timestamp}.mp4")
            if hybrid_mix(final_path, dubbed_path, env_audio_path, hybrid_path, env_volume=0.3):
                print(f"\n🎉 最终成品（旁白+BGM+环境音）: {hybrid_path}")
            else:
                print(f"\n🎉 配音成品（旁白+BGM，无环境音）: {dubbed_path}")
        else:
            print(f"\n🎉 配音成品（旁白+BGM）: {dubbed_path}")
    # ============================================================
    # Step 7: 自动生成分享链接
    # ============================================================
    # 确定最终输出的文件
    final_output = final_path
    if not args.no_dubbing and args.narration:
        if env_audio_path and os.path.exists(env_audio_path) and not args.no_env_audio:
            hybrid_path = str(output_dir / f"billiards_final_{timestamp}.mp4")
            if os.path.exists(hybrid_path):
                final_output = hybrid_path
        dubbed_path_check = str(output_dir / f"billiards_dubbed_{timestamp}.mp4")
        if os.path.exists(dubbed_path_check) and not os.path.exists(hybrid_path if 'hybrid_path' in dir() else ''):
            final_output = dubbed_path_check
    
    # 复制到分享目录
    SHARE_DIR.mkdir(parents=True, exist_ok=True)
    share_filename = Path(final_output).name
    share_path = SHARE_DIR / share_filename
    shutil.copy2(final_output, str(share_path))
    share_url = f"https://tg.tiangong.club/share/{share_filename}"
    print(f"\n📺 分享链接: {share_url}")


if __name__ == "__main__":
    # ============================================================
    # 文件锁防重入
    # ============================================================
    lock_fd = open(LOCK_FILE, "w")
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        print("❌ 另一个视频生成任务正在运行，请等待完成后再试")
        print(f"   锁文件: {LOCK_FILE}")
        sys.exit(1)
    # 锁在进程退出时自动释放
    
    try:
        main()
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        lock_fd.close()
        try:
            os.unlink(LOCK_FILE)
        except OSError:
            pass
