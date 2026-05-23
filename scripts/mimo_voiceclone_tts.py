#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
小米 MiMo-V2.5-TTS-VoiceClone 本地语音生成脚本。

使用方式：
  export MIMO_API_KEY="你的 API Key"
  python3 scripts/mimo_voiceclone_tts.py "你今天还好吗?"
"""

import argparse
import base64
import json
import mimetypes
import os
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


API_URL = "https://api.xiaomimimo.com/v1/chat/completions"
MODEL = "mimo-v2.5-tts-voiceclone"
DEFAULT_VOICE_FILE = "诺艾尔.wav"
DEFAULT_OUTPUT_DIR = "outputs"


def build_parser() -> argparse.ArgumentParser:
    """构建命令行参数解析器。"""
    parser = argparse.ArgumentParser(description="使用小米 MiMo 音色复刻模型生成语音")
    parser.add_argument("text", help="需要合成的文本，必须放在 assistant 消息中")
    parser.add_argument(
        "-v",
        "--voice",
        default=DEFAULT_VOICE_FILE,
        help=f"音色样本文件路径，默认：{DEFAULT_VOICE_FILE}",
    )
    parser.add_argument(
        "-o",
        "--output",
        default=None,
        help="输出 wav 文件路径；不传则自动保存到 outputs 目录",
    )
    parser.add_argument(
        "-i",
        "--instruction",
        default="",
        help="可选的语气/风格控制指令，会放在 user 消息中",
    )
    return parser


def build_output_path(output: Optional[str]) -> Path:
    """根据用户输入生成输出文件路径。"""
    if output:
        return Path(output).expanduser().resolve()

    # 使用时间戳避免覆盖历史生成结果。
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return Path(DEFAULT_OUTPUT_DIR).resolve() / f"mimo_voiceclone_{timestamp}.wav"


def read_voice_data_url(voice_path: Path) -> str:
    """读取音色样本并转换为接口需要的 data URL。"""
    if not voice_path.exists():
        raise FileNotFoundError(f"音色样本不存在：{voice_path}")

    mime_type = mimetypes.guess_type(voice_path.name)[0] or "audio/wav"
    voice_base64 = base64.b64encode(voice_path.read_bytes()).decode("utf-8")
    return f"data:{mime_type};base64,{voice_base64}"


def request_tts(api_key: str, text: str, voice_data_url: str, instruction: str) -> bytes:
    """调用 MiMo 音色复刻接口并返回生成的音频字节。"""
    payload = {
        "model": MODEL,
        "messages": [
            {
                "role": "user",
                "content": instruction,
            },
            {
                "role": "assistant",
                "content": text,
            },
        ],
        "audio": {
            "format": "wav",
            "voice": voice_data_url,
        },
    }

    # 官方文档要求使用 api-key header，而不是 OpenAI 常见的 Authorization。
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={
            "api-key": api_key,
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request, timeout=120) as response:
            response_body = response.read().decode("utf-8")
    except HTTPError as error:
        error_body = error.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"接口调用失败，HTTP {error.code}：{error_body}") from error
    except URLError as error:
        raise RuntimeError(f"接口网络请求失败：{error}") from error

    response_json = json.loads(response_body)
    try:
        audio_base64 = response_json["choices"][0]["message"]["audio"]["data"]
    except (KeyError, IndexError, TypeError) as error:
        formatted = json.dumps(response_json, ensure_ascii=False, indent=2)
        raise RuntimeError(f"接口返回中没有找到音频数据：\n{formatted}") from error

    return base64.b64decode(audio_base64)


def main() -> int:
    """读取参数、调用接口并保存生成音频。"""
    args = build_parser().parse_args()
    api_key = os.environ.get("MIMO_API_KEY")
    if not api_key:
        print("缺少环境变量 MIMO_API_KEY，请先执行：export MIMO_API_KEY=\"你的 API Key\"", file=sys.stderr)
        return 2

    voice_path = Path(args.voice).expanduser().resolve()
    output_path = build_output_path(args.output)

    try:
        # 先把本地音色样本转成 data URL，再发给音色复刻模型。
        voice_data_url = read_voice_data_url(voice_path)
        audio_bytes = request_tts(api_key, args.text, voice_data_url, args.instruction)

        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_bytes(audio_bytes)
    except Exception as error:
        print(str(error), file=sys.stderr)
        return 1

    print(f"语音已生成：{output_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
