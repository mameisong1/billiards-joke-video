#!/usr/bin/env python3
"""
台球厅段子视频一键生成脚本
天宫国际台球城 - 30秒段子短视频

用法:
  # 全自动（自动搜段子、自动从天宫接口获取助教/包房照片）
  python3 run.py --output /root/video

  # 指定段子文本
  python3 run.py --joke "段子内容..." --output /root/video

  # 带参考图（跳过自动获取）
  python3 run.py --scene-images "https://..." --person-images "https://..." --output /root/video

  # 跳过配音
  python3 run.py --no-dubbing --output /root/video
"""

import argparse
import json
import os
import random
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


def get_coach_photo(token):
    """从助教相册随机获取一张人物照片URL"""
    try:
        # 获取助教列表
        coaches_data = tiangong_api_get("/api/public/coaches", token)
        coaches = coaches_data.get("data", [])

        # 筛选有封面照的助教
        with_cover = [c for c in coaches if c.get("cover")]
        if not with_cover:
            print("  ⚠️ 没有找到有照片的助教")
            return ""

        # 优先选人气助教
        popular = [c for c in with_cover if c.get("is_popular") == 1]
        pool = popular if popular else with_cover

        # 随机选一位
        coach = random.choice(pool)
        coach_no = coach["coach_no"]
        stage_name = coach.get("stage_name", "?")
        print(f"  🎯 选中助教: {stage_name} (编号{coach_no})")

        # 获取详情拿相册
        detail = tiangong_api_get(f"/api/public/coaches/{coach_no}", token)
        photos = detail.get("data", {}).get("photos", [])

        if photos:
            print(f"  📸 助教相册共{len(photos)}张，使用第一张")
            return photos[0]
        elif coach.get("cover"):
            print(f"  📸 使用封面照")
            return coach["cover"]
        else:
            print(f"  ⚠️ 助教{stage_name}无照片可用")
            return ""
    except Exception as e:
        print(f"  ⚠️ 获取助教照片失败: {e}")
        return ""


def get_room_photo(token):
    """从包房相册随机获取一张场景照片URL"""
    try:
        # 获取包房列表
        rooms_data = tiangong_api_get("/api/public/vip-rooms", token)
        rooms = rooms_data.get("data", [])

        # 筛选有封面照的包房
        with_cover = [r for r in rooms if r.get("cover")]
        if not with_cover:
            print("  ⚠️ 没有找到有照片的包房")
            return ""

        # 随机选一间
        room = random.choice(with_cover)
        room_id = room["id"]
        room_name = room.get("name", "?")
        print(f"  🏠 选中包房: {room_name} (ID:{room_id})")

        # 获取详情拿相册
        detail = tiangong_api_get(f"/api/public/vip-rooms/{room_id}", token)
        photos = detail.get("data", {}).get("photos", [])

        if photos:
            print(f"  📸 包房相册共{len(photos)}张，使用第一张")
            return photos[0]
        elif room.get("cover"):
            print(f"  📸 使用封面照")
            return room["cover"]
        else:
            print(f"  ⚠️ 包房{room_name}无照片可用")
            return ""
    except Exception as e:
        print(f"  ⚠️ 获取包房照片失败: {e}")
        return ""


def auto_fetch_ref_images(person_images, scene_images):
    """
    自动获取参考图。
    优先级：用户传入 > 天宫数据接口 > 空列表（纯文生）
    返回 (person_urls, scene_urls)
    """
    person_urls = list(person_images) if person_images else []
    scene_urls = list(scene_images) if scene_images else []

    need_person = not person_urls
    need_scene = not scene_urls

    if not need_person and not need_scene:
        print("  ✅ 用户已提供所有参考图，无需自动获取")
        return person_urls, scene_urls

    print("\n🔍 自动获取参考图（天宫数据接口）...")
    token = get_tiangong_token()
    if not token:
        print("  ⚠️ Token获取失败，将使用纯文生视频模式")
        return person_urls, scene_urls

    if need_person:
        photo = get_coach_photo(token)
        if photo:
            person_urls.append(photo)
            print(f"  ✅ 人物参考图: {photo[:80]}...")
        else:
            print("  ⚠️ 未获取到人物参考图，将纯文生")

    if need_scene:
        photo = get_room_photo(token)
        if photo:
            scene_urls.append(photo)
            print(f"  ✅ 场景参考图: {photo[:80]}...")
        else:
            print("  ⚠️ 未获取到场景参考图，将纯文生")

    return person_urls, scene_urls


# ============================================================
# Seedance 2.0 API 直接调用
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
    """调用火山引擎 API"""
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            return json.loads(error_body)
        except json.JSONDecodeError:
            return {"error": {"message": error_body, "code": str(e.code)}}


def create_task(api_key, prompt, first_frame_url=None, ref_image_urls=None):
    """创建 Seedance 2.0 视频生成任务"""
    content = [{"type": "text", "text": prompt}]

    # 参考图（3段都传）
    if ref_image_urls:
        for img_url in ref_image_urls:
            content.append({
                "type": "image_url",
                "image_url": {"url": img_url},
                "role": "reference_image"
            })

    # 尾帧续拍（段2/段3）
    if first_frame_url:
        content.append({
            "type": "image_url",
            "image_url": {"url": first_frame_url},
            "role": "first_frame"
        })

    body = {
        "model": MODEL_ID,
        "content": content,
        "resolution": RESOLUTION,
        "duration": SEGMENT_DURATION,
        "ratio": RATIO,
        "return_last_frame": True,
        "generate_audio": True,
    }

    result = api_call("POST", BASE_URL, body, api_key)
    if "error" in result:
        print(f"❌ 创建任务失败: {result['error']}")
        return None
    return result.get("id")


def wait_task(api_key, task_id, timeout=360):
    """等待任务完成，返回完整结果"""
    url = f"{BASE_URL}/{task_id}"
    start = time.time()
    while time.time() - start < timeout:
        result = api_call("GET", url, None, api_key)
        status = result.get("status", "unknown")
        elapsed = int(time.time() - start)
        print(f"  ⏳ [{elapsed}s] {status}", flush=True)
        if status == "succeeded":
            return result
        elif status in ("failed", "expired"):
            print(f"❌ 任务{status}: {result.get('error', {})}")
            return result
        time.sleep(15)
    print("⏰ 超时")
    return None


def download_video(url, filepath):
    """下载视频到本地"""
    try:
        urllib.request.urlretrieve(url, str(filepath))
        return True
    except Exception as e:
        print(f"  ❌ 下载失败: {e}")
        return False


def generate_segment(api_key, seg_num, prompt, first_frame_url=None, ref_image_urls=None):
    """生成单段视频，返回 (video_path, last_frame_url)"""
    label = {1: "钩子", 2: "展开", 3: "反转"}.get(seg_num, f"段{seg_num}")
    print(f"\n{'='*60}")
    print(f"🎬 段{seg_num}·{label} ({SEGMENT_DURATION}s)")
    print(f"{'='*60}")
    print(f"  Prompt: {prompt[:80]}...")
    if first_frame_url:
        print(f"  续拍: 尾帧衔接")
    if ref_image_urls:
        print(f"  参考图: {len(ref_image_urls)}张")

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
    if not download_video(video_url, video_path):
        return None, None

    return str(video_path), last_frame_url


# ============================================================
# 拼接与配音
# ============================================================
def concat_videos(seg_paths, output_path):
    """拼接3段视频"""
    print(f"\n{'='*60}")
    print(f"🔄 拼接 {len(seg_paths)} 段视频")
    print(f"{'='*60}")
    cmd = ["python3", CONCAT_PY, "--inputs"] + seg_paths + ["--output", output_path]
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ 拼接失败: {result.stderr[-300:]}")
        return False
    return True


def dub_video(video_path, narration_text, output_path):
    """配音"""
    print(f"\n{'='*60}")
    print(f"🎙️ 配音处理")
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
    result = subprocess.run(cmd, capture_output=True, text=True)
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ 配音失败: {result.stderr[-300:]}")
        return False
    return True


# ============================================================
# 主流程
# ============================================================
def main():
    parser = argparse.ArgumentParser(description="台球厅段子视频一键生成 - 天宫国际台球城")
    parser.add_argument("--joke", type=str, default="", help="段子文本（不传则AI自动搜索）")
    parser.add_argument("--scene-images", nargs="*", default=[], help="场景图URL列表（不传则自动从包房相册获取）")
    parser.add_argument("--person-images", nargs="*", default=[], help="人物图URL列表（不传则自动从助教相册获取）")
    parser.add_argument("--style", default="搞笑", help="视频风格（默认: 搞笑）")
    parser.add_argument("--no-dubbing", action="store_true", help="跳过配音")
    parser.add_argument("--output", default="/root/video", help="输出目录")
    parser.add_argument("--prompts", nargs=3, help="3段视频的prompt文本（由AI生成）")
    parser.add_argument("--narration", type=str, default="", help="完整旁白文本（配音用）")
    args = parser.parse_args()

    api_key = get_api_key()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ============================================================
    # Step 1: 自动获取参考图
    # ============================================================
    person_urls, scene_urls = auto_fetch_ref_images(args.person_images, args.scene_images)
    ref_image_urls = person_urls + scene_urls

    # ============================================================
    # Step 2: 生成3段视频（链式续拍）
    # ============================================================
    if not args.prompts:
        print("❌ 需要通过 --prompts 传入3段视频prompt")
        print("   AI Agent 先搜索段子、编写脚本，然后调用本脚本")
        sys.exit(1)

    prompts = args.prompts
    print(f"\n🎱 天宫国际台球城 - 段子视频生成")
    print(f"📅 {timestamp}")
    print(f"📐 {RATIO} | {RESOLUTION} | {SEGMENT_DURATION}s×3 = {SEGMENT_DURATION*3}s")
    if ref_image_urls:
        print(f"🖼️ 参考图: {len(ref_image_urls)}张 ({len(person_urls)}人物 + {len(scene_urls)}场景)")
    else:
        print(f"🖼️ 参考图: 无（纯文生视频）")

    seg_paths = []
    last_frame_url = None

    for i in range(3):
        seg_num = i + 1
        video_path, last_frame_url = generate_segment(
            api_key, seg_num, prompts[i],
            first_frame_url=last_frame_url,
            ref_image_urls=ref_image_urls if ref_image_urls else None,
        )
        if not video_path:
            print(f"❌ 第{seg_num}段生成失败，流程中止")
            sys.exit(1)
        seg_paths.append(video_path)

    # ============================================================
    # Step 3: 拼接
    # ============================================================
    final_path = str(output_dir / f"billiards_{timestamp}.mp4")
    if not concat_videos(seg_paths, final_path):
        sys.exit(1)

    print(f"\n✅ 视频拼接完成: {final_path}")

    # ============================================================
    # Step 4: 配音（可选）
    # ============================================================
    if not args.no_dubbing and args.narration:
        dubbed_path = str(output_dir / f"billiards_dubbed_{timestamp}.mp4")
        if dub_video(final_path, args.narration, dubbed_path):
            print(f"\n🎉 最终成品: {dubbed_path}")
        else:
            print(f"\n🎉 视频成品（未配音）: {final_path}")
    else:
        print(f"\n🎉 视频成品: {final_path}")


if __name__ == "__main__":
    main()
