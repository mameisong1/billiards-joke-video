#!/usr/bin/env python3
"""
台球厅段子视频一键生成脚本
天宫国际台球城 - 30秒段子短视频

用法:
  # 全自动（自动搜段子、无参考图）
  python3 run.py --output /root/video

  # 指定段子文本
  python3 run.py --joke "段子内容..." --output /root/video

  # 带参考图
  python3 run.py --scene-images "https://..." --person-images "https://..." --output /root/video

  # 跳过配音
  python3 run.py --no-dubbing --output /root/video
"""

import argparse
import json
import os
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


def get_api_key():
    """获取 ARK API Key"""
    key = os.environ.get("ARK_API_KEY")
    if not key:
        # 尝试从 .bashrc 读取
        bashrc = os.path.expanduser("~/.bashrc")
        if os.path.exists(bashrc):
            with open(bashrc) as f:
                for line in f:
                    if "ARK_API_KEY" in line and "export" in line:
                        key = line.split('"')[1] if '"' in line else line.split("'")[1]
                        os.environ["ARK_API_KEY"] = key
                        break
    if not key:
        print("❌ 无法获取 ARK_API_KEY，请设置环境变量或检查 ~/.bashrc")
        sys.exit(1)
    return key


def api_call(method, url, data=None, api_key=None):
    """调用火山引擎 API"""
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    body = json.dumps(data).encode("utf-8") if data else None
    req = urllib.request.Request(url, data=body, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        error_body = e.read().decode("utf-8", errors="replace")
        try:
            err = json.loads(error_body)
            return err
        except json.JSONDecodeError:
            return {"error": {"message": error_body, "code": str(e.code)}}


def create_task(api_key, prompt, first_frame_url=None, ref_image_urls=None):
    """创建 Seedance 2.0 视频生成任务"""
    content = [{"type": "text", "text": prompt}]

    # 参考图（人物/场景，3段都传）
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
        print(f"  ⏳ [{elapsed}s] 状态: {status}", flush=True)

        if status == "succeeded":
            return result
        elif status in ("failed", "expired"):
            print(f"❌ 任务{status}: {result.get('error', {})}")
            return result
        time.sleep(15)

    print(f"⏰ 任务超时 ({timeout}s)")
    return None


def download_video(url, filepath):
    """下载视频到本地"""
    try:
        urllib.request.urlretrieve(url, str(filepath))
        print(f"  📥 已下载: {filepath}")
        return True
    except Exception as e:
        print(f"  ❌ 下载失败: {e}")
        return False


def generate_segment(api_key, seg_num, prompt, first_frame_url=None, ref_image_urls=None):
    """生成单段视频，返回 (video_path, last_frame_url) 或 (None, None)"""
    print(f"\n{'='*60}")
    print(f"🎬 生成第{seg_num}段视频 ({SEGMENT_DURATION}s)")
    print(f"{'='*60}")
    print(f"  Prompt: {prompt[:80]}...")
    if first_frame_url:
        print(f"  续拍: 使用上一段尾帧")
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
    print(f"  ✅ 生成成功! Tokens: {usage.get('total_tokens', '?')}")

    # 下载视频
    VIDEO_DIR.mkdir(parents=True, exist_ok=True)
    video_path = VIDEO_DIR / f"seg{seg_num}_{task_id}.mp4"
    if not download_video(video_url, video_path):
        return None, None

    return str(video_path), last_frame_url


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


def main():
    parser = argparse.ArgumentParser(description="台球厅段子视频一键生成 - 天宫国际台球城")
    parser.add_argument("--joke", type=str, default="", help="段子文本（不传则提示AI搜索）")
    parser.add_argument("--scene-images", nargs="*", default=[], help="场景图URL列表")
    parser.add_argument("--person-images", nargs="*", default=[], help="人物图URL列表")
    parser.add_argument("--style", default="搞笑", help="视频风格（默认: 搞笑）")
    parser.add_argument("--no-dubbing", action="store_true", help="跳过配音")
    parser.add_argument("--output", default="/root/video", help="输出目录")
    args = parser.parse_args()

    api_key = get_api_key()
    output_dir = Path(args.output)
    output_dir.mkdir(parents=True, exist_ok=True)

    # 参考图合并
    ref_images = args.person_images + args.scene_images

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    # ============================================================
    # 此脚本只负责视频生成和拼接部分
    # 段子搜索和脚本编写由 AI Agent 在调用此脚本前完成
    # AI Agent 需要通过 --prompts 参数传入3段 prompt
    # ============================================================
    parser.add_argument("--prompts", nargs=3, help="3段视频的prompt文本（由AI生成）")
    parser.add_argument("--narration", type=str, default="", help="完整旁白文本（配音用）")

    # 重新解析（因为添加了参数）
    args = parser.parse_args()

    if not args.prompts:
        print("❌ 需要通过 --prompts 传入3段视频prompt")
        print("   使用方式: AI Agent 先搜索段子、编写脚本，然后调用本脚本")
        sys.exit(1)

    prompts = args.prompts
    print(f"\n🎱 天宫国际台球城 - 段子视频生成")
    print(f"📅 {timestamp}")
    print(f"📐 {RATIO} | {RESOLUTION} | {SEGMENT_DURATION}s×3 = {SEGMENT_DURATION*3}s")

    # ============================================================
    # Step 1-2: 生成3段视频（链式续拍）
    # ============================================================
    seg_paths = []
    last_frame_url = None

    for i in range(3):
        seg_num = i + 1
        video_path, last_frame_url = generate_segment(
            api_key, seg_num, prompts[i],
            first_frame_url=last_frame_url,
            ref_image_urls=ref_images if ref_images else None,
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
