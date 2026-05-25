#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
MiMo 音色复刻本地网页服务。

启动方式：
  export MIMO_API_KEY="你的 API Key"
  python3 server.py
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import random
import secrets
import shutil
import string
import subprocess
import tempfile
import time
import uuid
import wave
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen


ROOT_DIR = Path(__file__).resolve().parent
STATIC_DIR = ROOT_DIR / "static"
DATA_DIR = ROOT_DIR / "data"
VOICE_CACHE_DIR = DATA_DIR / "voices"
OUTPUT_DIR = ROOT_DIR / "output"
HISTORY_FILE = DATA_DIR / "history.json"
USERS_FILE = DATA_DIR / "users.json"
TOKENS_FILE = DATA_DIR / "tokens.json"
DEFAULT_VOICE_FILE = None

API_URL = "https://api.xiaomimimo.com/v1/chat/completions"

# 三种 TTS 模型：分别对应「音色克隆」、「预置音色」、「音色设计」
MODEL_CLONE = "mimo-v2.5-tts-voiceclone"
MODEL_PRESET = "mimo-v2.5-tts"
MODEL_DESIGN = "mimo-v2.5-tts-voicedesign"

# 前端使用的简短模式名 → 实际 model id 的映射
MODEL_ID_BY_KEY = {
    "clone": MODEL_CLONE,
    "preset": MODEL_PRESET,
    "design": MODEL_DESIGN,
}

# 预置音色 ID 白名单（来自官方文档）
PRESET_VOICES = (
    "mimo_default",
    "冰糖", "茉莉", "苏打", "白桦",
    "Mia", "Chloe", "Milo", "Dean",
)

STREAM_SAMPLE_RATE = 24000

# 旧数据迁移用（不再自动创建默认账号）
_MIGRATE_FALLBACK_USER = "_migrated"

# 密码长度下限
PASSWORD_MIN_LEN = 4

# pbkdf2 迭代次数（兼顾安全和本地启动速度）
PBKDF2_ITERS = 200_000

# 并发锁：保护多线程下的 JSON 文件读写
_users_lock = Lock()
_tokens_lock = Lock()
_history_lock = Lock()


# ============================================================================
# 通用辅助
# ============================================================================

def ensure_runtime_dirs() -> None:
    """创建本地运行所需目录与基础文件。"""
    for path in (DATA_DIR, VOICE_CACHE_DIR, OUTPUT_DIR):
        path.mkdir(parents=True, exist_ok=True)
    if not HISTORY_FILE.exists():
        HISTORY_FILE.write_text("[]", encoding="utf-8")
    if not USERS_FILE.exists():
        USERS_FILE.write_text(json.dumps({"users": {}}, ensure_ascii=False, indent=2), encoding="utf-8")
    if not TOKENS_FILE.exists():
        TOKENS_FILE.write_text(json.dumps({"tokens": {}}, ensure_ascii=False, indent=2), encoding="utf-8")


def now_text() -> str:
    """返回用于文件名和展示的当前时间文本。"""
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def random_suffix(length: int = 6) -> str:
    """生成短随机串，避免同一秒内文件名冲突。"""
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.choice(alphabet) for _ in range(length))


def json_response(handler: BaseHTTPRequestHandler, status: int, payload: dict | list) -> None:
    """输出 JSON 响应。"""
    body = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def safe_username(value: str) -> bool:
    """校验用户名格式：3-32 位，字母数字下划线。"""
    if not value or not isinstance(value, str):
        return False
    if len(value) < 3 or len(value) > 32:
        return False
    return all(ch.isalnum() or ch == "_" for ch in value)


def safe_original_name(filename: str) -> str:
    """清理浏览器上传文件名，避免路径穿越。"""
    name = Path(filename or "voice.wav").name
    return name.replace("/", "_").replace("\\", "_")


# ============================================================================
# Multipart/form-data 解析（替代 Python 3.13+ 移除的 cgi 模块）
# ============================================================================

class FormPart:
    """单个 multipart 字段。filename 为空表示纯文本字段。"""
    __slots__ = ("name", "filename", "content_type", "data")

    def __init__(self, name: str, filename: str, content_type: str, data: bytes) -> None:
        self.name = name
        self.filename = filename
        self.content_type = content_type
        self.data = data

    def text(self) -> str:
        try:
            return self.data.decode("utf-8")
        except UnicodeDecodeError:
            return self.data.decode("utf-8", errors="replace")


def _parse_content_disposition(header: str) -> tuple[str, str]:
    """从 Content-Disposition 头解析 (name, filename)。"""
    name = ""
    filename = ""
    for piece in header.split(";"):
        piece = piece.strip()
        if piece.startswith("name="):
            name = piece[5:].strip().strip('"')
        elif piece.startswith("filename="):
            filename = piece[9:].strip().strip('"')
    return name, filename


def _extract_boundary(content_type: str) -> bytes:
    """从 Content-Type 头中取出 boundary。"""
    if not content_type:
        raise ValueError("缺少 Content-Type 头。")
    for piece in content_type.split(";"):
        piece = piece.strip()
        if piece.lower().startswith("boundary="):
            value = piece.split("=", 1)[1].strip().strip('"')
            if not value:
                raise ValueError("multipart 边界为空。")
            return value.encode("utf-8")
    raise ValueError("Content-Type 中没有 boundary 参数。")


def parse_multipart(body: bytes, content_type: str) -> dict[str, FormPart]:
    """把 multipart/form-data 主体拆成 {name: FormPart}。"""
    boundary = _extract_boundary(content_type)
    delimiter = b"--" + boundary
    parts: dict[str, FormPart] = {}

    # 按 boundary 切分；首尾分别是空白和 "--" 结束符
    sections = body.split(delimiter)
    for section in sections:
        # 去掉每段的前后 \r\n
        if section.startswith(b"\r\n"):
            section = section[2:]
        if section.endswith(b"\r\n"):
            section = section[:-2]
        # 跳过空段和结束段 "--"
        if not section or section == b"--":
            continue

        header_blob, _, data = section.partition(b"\r\n\r\n")
        if not header_blob:
            continue

        headers: dict[str, str] = {}
        for line in header_blob.split(b"\r\n"):
            try:
                key, _, value = line.decode("utf-8", errors="replace").partition(":")
            except Exception:
                continue
            if key:
                headers[key.strip().lower()] = value.strip()

        disposition = headers.get("content-disposition", "")
        name, filename = _parse_content_disposition(disposition)
        if not name:
            continue
        parts[name] = FormPart(
            name=name,
            filename=filename,
            content_type=headers.get("content-type", ""),
            data=data,
        )

    return parts


def get_form_text(form: dict[str, FormPart], name: str, default: str = "") -> str:
    """从解析后的 multipart 表单中取一个文本字段（已 strip）。"""
    part = form.get(name)
    if part is None:
        return default
    return part.text().strip() or default


# ============================================================================
# 用户与认证
# ============================================================================

def hash_password(password: str, salt_hex: str | None = None) -> tuple[str, str]:
    """密码哈希；返回 (salt_hex, hash_hex)。salt 不传则新生成。"""
    if salt_hex is None:
        salt_bytes = secrets.token_bytes(16)
        salt_hex = salt_bytes.hex()
    else:
        salt_bytes = bytes.fromhex(salt_hex)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt_bytes, PBKDF2_ITERS)
    return salt_hex, digest.hex()


def verify_password(password: str, salt_hex: str, expected_hash_hex: str) -> bool:
    """常量时间比较密码。"""
    _, actual_hash_hex = hash_password(password, salt_hex)
    return secrets.compare_digest(actual_hash_hex, expected_hash_hex)


def load_users() -> dict:
    """读取 users.json。"""
    try:
        return json.loads(USERS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return {"users": {}}


def save_users(data: dict) -> None:
    """写入 users.json。"""
    USERS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def create_user(username: str, password: str) -> None:
    """创建用户。重复用户名会抛 ValueError。"""
    if not safe_username(username):
        raise ValueError("用户名仅支持 3-32 位字母、数字或下划线。")
    if not isinstance(password, str) or len(password) < PASSWORD_MIN_LEN:
        raise ValueError(f"密码至少需要 {PASSWORD_MIN_LEN} 位。")

    with _users_lock:
        data = load_users()
        users = data.setdefault("users", {})
        if username in users:
            raise ValueError("用户名已存在，请直接登录或换一个。")
        salt_hex, hash_hex = hash_password(password)
        users[username] = {
            "salt": salt_hex,
            "password_hash": hash_hex,
            "api_key": "",  # 每个用户绑定自己的 MiMo API Key
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_users(data)
    # 为新用户准备音频输出目录
    (OUTPUT_DIR / username).mkdir(parents=True, exist_ok=True)


def get_user_api_key(username: str) -> str:
    """读取指定用户保存的 API Key（未配置则返回空串）。"""
    data = load_users()
    user = (data.get("users") or {}).get(username)
    if not user:
        return ""
    return str(user.get("api_key") or "").strip()


def set_user_api_key(username: str, api_key: str) -> None:
    """更新指定用户的 API Key（空串表示清除）。"""
    with _users_lock:
        data = load_users()
        users = data.setdefault("users", {})
        if username not in users:
            raise ValueError("用户不存在。")
        users[username]["api_key"] = (api_key or "").strip()
        save_users(data)


def mask_api_key(api_key: str) -> str:
    """脱敏展示：保留前 3 后 4 位，中间替换为 ***。"""
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:3]}***{api_key[-4:]}"


def authenticate(username: str, password: str) -> bool:
    """校验用户名/密码是否正确。"""
    data = load_users()
    user = (data.get("users") or {}).get(username)
    if not user:
        return False
    return verify_password(password, user.get("salt", ""), user.get("password_hash", ""))


def load_tokens() -> dict:
    """读取 tokens.json。"""
    try:
        return json.loads(TOKENS_FILE.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, FileNotFoundError):
        return {"tokens": {}}


def save_tokens(data: dict) -> None:
    """写入 tokens.json。"""
    TOKENS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def issue_token(username: str) -> str:
    """为指定用户生成一个 token 并落盘。"""
    token = secrets.token_urlsafe(32)
    with _tokens_lock:
        data = load_tokens()
        tokens = data.setdefault("tokens", {})
        tokens[token] = {
            "username": username,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        }
        save_tokens(data)
    return token


def revoke_token(token: str) -> None:
    """删除一个 token（退出登录时使用）。"""
    with _tokens_lock:
        data = load_tokens()
        tokens = data.setdefault("tokens", {})
        if token in tokens:
            del tokens[token]
            save_tokens(data)


def username_from_token(token: str) -> str:
    """根据 token 查 username；查不到返回空串。"""
    if not token:
        return ""
    data = load_tokens()
    info = (data.get("tokens") or {}).get(token)
    if not info:
        return ""
    return str(info.get("username") or "")


def extract_bearer_token(handler: BaseHTTPRequestHandler) -> str:
    """从 Authorization 头或 ?token= 查询参数中提取 Bearer token。"""
    auth_header = handler.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        return auth_header[7:].strip()
    # 资源类请求 (audio) 通过 query 参数携带 token
    query = urlparse(handler.path).query
    params = parse_qs(query)
    values = params.get("token") or []
    return values[0].strip() if values else ""


def authenticate_request(handler: BaseHTTPRequestHandler) -> str:
    """认证当前请求，成功返回 username，失败返回空串。"""
    token = extract_bearer_token(handler)
    return username_from_token(token)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict:
    """读取 application/json 请求体。"""
    length = int(handler.headers.get("Content-Length") or 0)
    if length <= 0:
        return {}
    raw = handler.rfile.read(length)
    try:
        return json.loads(raw.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return {}


# ============================================================================
# 历史记录（按 owner 隔离）
# ============================================================================

def read_history_all() -> list[dict]:
    """读取完整历史（含所有用户）。仅迁移和管理用。"""
    ensure_runtime_dirs()
    try:
        return json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []


def write_history_all(records: list[dict]) -> None:
    """覆盖写整个历史文件。"""
    HISTORY_FILE.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def read_history(owner: str) -> list[dict]:
    """读取指定用户的历史。"""
    return [r for r in read_history_all() if r.get("owner") == owner]


def append_history(record: dict) -> None:
    """追加一条记录到历史；按用户分别截留前 200 条。"""
    owner = record.get("owner", "")
    with _history_lock:
        all_records = read_history_all()
        all_records.insert(0, record)
        # 按 owner 分组保留前 200 条
        kept: list[dict] = []
        owner_count: dict[str, int] = {}
        for rec in all_records:
            o = rec.get("owner", "")
            owner_count[o] = owner_count.get(o, 0) + 1
            if owner_count[o] <= 200:
                kept.append(rec)
        write_history_all(kept)


def _audio_path_from_url(url: str) -> Path | None:
    """根据 audio_url（形如 /audio/<user>/<file>）反查本地路径。"""
    if not isinstance(url, str) or not url.startswith("/audio/"):
        return None
    rel = url.removeprefix("/audio/")
    parts = [p for p in rel.split("/") if p]
    if len(parts) == 2:
        return OUTPUT_DIR / parts[0] / Path(parts[1]).name
    if len(parts) == 1:  # 兼容历史的 /audio/<file>
        return OUTPUT_DIR / Path(parts[0]).name
    return None


def delete_history_record(owner: str, record_id: str) -> bool:
    """删除指定用户的单条历史，同时清理音频文件。"""
    with _history_lock:
        all_records = read_history_all()
        kept: list[dict] = []
        removed = False
        for rec in all_records:
            if not removed and rec.get("id") == record_id and rec.get("owner") == owner:
                audio_path = _audio_path_from_url(str(rec.get("audio_url") or ""))
                if audio_path and audio_path.exists():
                    audio_path.unlink()
                removed = True
                continue
            kept.append(rec)
        if removed:
            write_history_all(kept)
        return removed


def clear_history_records(owner: str) -> int:
    """清空指定用户的全部历史，并删除对应音频文件。"""
    with _history_lock:
        all_records = read_history_all()
        kept: list[dict] = []
        removed_count = 0
        for rec in all_records:
            if rec.get("owner") == owner:
                audio_path = _audio_path_from_url(str(rec.get("audio_url") or ""))
                if audio_path and audio_path.exists():
                    audio_path.unlink()
                removed_count += 1
            else:
                kept.append(rec)
        write_history_all(kept)
    return removed_count


# ============================================================================
# 启动迁移：把旧版本无归属数据归入迁移账号
# ============================================================================

def migrate_existing_data() -> None:
    """首次启动时把无 owner 的旧记录归到迁移账号，并把 output/*.wav|*.mp3 移到子目录。"""
    ensure_runtime_dirs()

    # 1. 历史记录：把无 owner 的归到迁移账号，并把 /audio/x.wav 改写为 /audio/<user>/x.wav
    records = read_history_all()
    changed = False
    for rec in records:
        if not rec.get("owner"):
            rec["owner"] = _MIGRATE_FALLBACK_USER
            changed = True
        # 旧版本没有 model 字段；统一标记为音色克隆
        if not rec.get("model"):
            rec["model"] = "clone"
            changed = True
        url = str(rec.get("audio_url") or "")
        if url.startswith("/audio/") and "/" not in url.removeprefix("/audio/"):
            file_name = url.removeprefix("/audio/")
            rec["audio_url"] = f"/audio/{_MIGRATE_FALLBACK_USER}/{file_name}"
            changed = True
    if changed:
        write_history_all(records)
        print(f"[migrate] 已将历史记录归属到 {_MIGRATE_FALLBACK_USER}")

    # 2. 物理文件：把 output/ 顶层的 wav/mp3 移动到 output/<迁移账号>/
    target_dir = OUTPUT_DIR / _MIGRATE_FALLBACK_USER
    target_dir.mkdir(parents=True, exist_ok=True)
    moved = 0
    for entry in OUTPUT_DIR.iterdir():
        if entry.is_file() and entry.suffix.lower() in (".wav", ".mp3"):
            dest = target_dir / entry.name
            if dest.exists():
                entry.unlink()  # 已有同名则丢弃旧的，避免覆盖
            else:
                entry.rename(dest)
            moved += 1
    if moved:
        print(f"[migrate] 已迁移 {moved} 个音频文件到 output/{DEFAULT_USER}/")


# ============================================================================
# 音频生成（保留原逻辑，输出路径按 owner 分目录）
# ============================================================================

def guess_audio_mime(path: Path) -> str:
    """根据文件后缀推断接口需要的音频 MIME 类型。"""
    suffix = path.suffix.lower()
    if suffix == ".mp3":
        return "audio/mpeg"
    if suffix == ".wav":
        return "audio/wav"
    return mimetypes.guess_type(path.name)[0] or "audio/wav"


def build_voice_data_url(voice_path: Path) -> str:
    """读取音色样本并转为 MiMo 接口需要的 data URL。"""
    if not voice_path.exists():
        raise FileNotFoundError(f"音色样本不存在：{voice_path}")
    voice_base64 = base64.b64encode(voice_path.read_bytes()).decode("utf-8")
    return f"data:{guess_audio_mime(voice_path)};base64,{voice_base64}"


def build_output_paths(owner: str, output_format: str) -> tuple[Path, Path]:
    """生成临时 wav 路径和最终输出路径（位于 output/<owner>/ 下）。"""
    owner_dir = OUTPUT_DIR / owner
    owner_dir.mkdir(parents=True, exist_ok=True)
    base_name = f"{now_text()}_{random_suffix()}"
    return owner_dir / f"{base_name}.wav", owner_dir / f"{base_name}.{output_format}"


def convert_wav_to_mp3(wav_path: Path, mp3_path: Path) -> None:
    """使用本机 ffmpeg 将 wav 转换为 mp3。"""
    ffmpeg = shutil.which("ffmpeg")
    if not ffmpeg:
        raise RuntimeError("未找到 ffmpeg，无法转换为 mp3，请先安装 ffmpeg。")

    command = [
        ffmpeg,
        "-y",
        "-i",
        str(wav_path),
        "-codec:a",
        "libmp3lame",
        "-q:a",
        "2",
        str(mp3_path),
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 转换 mp3 失败：{result.stderr.strip()}")


def write_pcm16_wav(pcm_bytes: bytes, wav_path: Path) -> None:
    """把 MiMo 流式返回的 24kHz PCM16LE mono 数据写成 wav。"""
    with wave.open(str(wav_path), "wb") as wav_file:
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(STREAM_SAMPLE_RATE)
        wav_file.writeframes(pcm_bytes)


def build_payload_clone(text: str, instruction: str, voice_data_url: str, mode: str) -> dict:
    """音色克隆：voice 字段传 base64 data URL；user 消息是风格指令。"""
    audio_format = "pcm16" if mode == "stream" else "wav"
    payload = {
        "model": MODEL_CLONE,
        "messages": [
            {"role": "user", "content": instruction},
            {"role": "assistant", "content": text},
        ],
        "audio": {
            "format": audio_format,
            "voice": voice_data_url,
        },
    }
    if mode == "stream":
        payload["stream"] = True
    return payload


def build_payload_preset(text: str, instruction: str, preset_voice: str, mode: str) -> dict:
    """预置音色：voice 是音色 ID（如 "Chloe"），user 消息是风格指令（可空）。"""
    audio_format = "pcm16" if mode == "stream" else "wav"
    payload = {
        "model": MODEL_PRESET,
        "messages": [
            {"role": "user", "content": instruction},
            {"role": "assistant", "content": text},
        ],
        "audio": {
            "format": audio_format,
            "voice": preset_voice,
        },
    }
    if mode == "stream":
        payload["stream"] = True
    return payload


def build_payload_design(text: str, voice_description: str, optimize_text: bool, mode: str) -> dict:
    """音色设计：user 消息是音色描述（必填）；可选 optimize_text_preview 智能润色文本。"""
    audio_format = "pcm16" if mode == "stream" else "wav"
    audio: dict = {"format": audio_format}
    if optimize_text:
        audio["optimize_text_preview"] = True
    payload = {
        "model": MODEL_DESIGN,
        "messages": [
            {"role": "user", "content": voice_description},
            {"role": "assistant", "content": text},
        ],
        "audio": audio,
    }
    if mode == "stream":
        payload["stream"] = True
    return payload


def call_mimo_json(api_key: str, payload: dict) -> bytes:
    """普通模式调用 MiMo 接口并返回 wav 字节。"""
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=180) as response:
        response_json = json.loads(response.read().decode("utf-8"))

    try:
        audio_base64 = response_json["choices"][0]["message"]["audio"]["data"]
    except (KeyError, IndexError, TypeError) as error:
        formatted = json.dumps(response_json, ensure_ascii=False, indent=2)
        raise RuntimeError(f"接口返回中没有找到音频数据：{formatted}") from error
    return base64.b64decode(audio_base64)


def parse_stream_audio_line(line: str) -> bytes:
    """从 SSE data 行中解析一段 base64 音频。"""
    if not line.startswith("data:"):
        return b""
    data = line[5:].strip()
    if not data or data == "[DONE]":
        return b""

    event = json.loads(data)
    choices = event.get("choices") or []
    if not choices:
        return b""
    audio = choices[0].get("delta", {}).get("audio")
    if not audio:
        return b""
    return base64.b64decode(audio["data"])


def call_mimo_stream(api_key: str, payload: dict) -> bytes:
    """流式兼容模式调用 MiMo 接口并拼接 PCM16 字节。"""
    request = Request(
        API_URL,
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"api-key": api_key, "Content-Type": "application/json"},
        method="POST",
    )

    chunks: list[bytes] = []
    with urlopen(request, timeout=180) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            chunk = parse_stream_audio_line(line)
            if chunk:
                chunks.append(chunk)
    if not chunks:
        raise RuntimeError("流式接口未返回音频数据。")
    return b"".join(chunks)


def normalize_api_error(error: Exception) -> str:
    """把 HTTP 或网络错误转换为页面可读的中文错误。"""
    if isinstance(error, HTTPError):
        body = error.read().decode("utf-8", errors="replace")
        return f"接口调用失败，HTTP {error.code}：{body}"
    if isinstance(error, URLError):
        return f"接口网络请求失败：{error}"
    return str(error)


def generate_audio(
    api_key: str,
    owner: str,
    model_key: str,
    text: str,
    instruction: str,
    voice_path: Path | None,
    preset_voice: str,
    optimize_text: bool,
    mode: str,
    output_format: str,
) -> dict:
    """根据 model_key 路由到不同模型，生成语音并保存为本地文件。"""
    if model_key == "clone":
        if voice_path is None:
            raise ValueError("音色克隆需要提供音色样本。")
        voice_data_url = build_voice_data_url(voice_path)
        payload = build_payload_clone(text, instruction, voice_data_url, mode)
    elif model_key == "preset":
        if preset_voice not in PRESET_VOICES:
            raise ValueError("预置音色不在支持列表中。")
        payload = build_payload_preset(text, instruction, preset_voice, mode)
    elif model_key == "design":
        if not instruction:
            raise ValueError("音色设计模式需要在描述里说明想要的音色风格。")
        payload = build_payload_design(text, instruction, optimize_text, mode)
    else:
        raise ValueError("未知的模型类型。")

    wav_path, output_path = build_output_paths(owner, output_format)

    try:
        if mode == "stream":
            pcm_bytes = call_mimo_stream(api_key, payload)
            write_pcm16_wav(pcm_bytes, wav_path)
        else:
            wav_path.write_bytes(call_mimo_json(api_key, payload))
        if output_format == "mp3":
            convert_wav_to_mp3(wav_path, output_path)
        elif output_path != wav_path:
            wav_path.replace(output_path)
    finally:
        if wav_path.exists() and wav_path != output_path:
            wav_path.unlink()

    return {
        "file_name": output_path.name,
        "file_path": str(output_path),
        "audio_url": f"/audio/{owner}/{output_path.name}",
    }


def save_uploaded_voice(form: dict[str, FormPart]) -> tuple[Path, str]:
    """保存上传音色；如果未上传则抛出错误。"""
    voice_part = form.get("voice")
    if voice_part is None or not voice_part.filename:
        raise ValueError("音色克隆模式需要上传音频样本。")

    original_name = safe_original_name(voice_part.filename)
    suffix = Path(original_name).suffix.lower()
    if suffix not in (".wav", ".mp3"):
        raise ValueError("音色样本只支持 wav 或 mp3 文件。")

    cached_name = f"{now_text()}_{uuid.uuid4().hex[:8]}_{original_name}"
    cached_path = VOICE_CACHE_DIR / cached_name
    cached_path.write_bytes(voice_part.data)
    return cached_path, original_name


# ============================================================================
# HTTP 处理器
# ============================================================================

class MimoRequestHandler(BaseHTTPRequestHandler):
    """MiMo 本地网页请求处理器。"""

    server_version = "MimoVoiceCloneLocal/2.0"

    # ---------------- 路由 ----------------

    def do_HEAD(self) -> None:
        """处理静态资源和音频文件的 HEAD 探测请求。"""
        path = urlparse(self.path).path
        if path == "/":
            self.serve_static(STATIC_DIR / "index.html", head_only=True)
            return
        if path == "/login":
            self.serve_static(STATIC_DIR / "login.html", head_only=True)
            return
        if path.startswith("/audio/"):
            self.serve_audio(path.removeprefix("/audio/"), head_only=True)
            return
        if path.startswith("/static/"):
            self.serve_static(STATIC_DIR / path.removeprefix("/static/"), head_only=True)
            return
        self.send_response(HTTPStatus.NOT_FOUND)
        self.end_headers()

    def do_GET(self) -> None:
        """处理页面、历史记录和音频文件读取。"""
        path = urlparse(self.path).path
        if path == "/":
            self.serve_static(STATIC_DIR / "index.html")
            return
        if path == "/login":
            self.serve_static(STATIC_DIR / "login.html")
            return
        if path == "/api/auth/me":
            self.handle_me()
            return
        if path == "/api/settings":
            self.handle_settings_get()
            return
        if path == "/api/history":
            self.handle_history_get()
            return
        if path.startswith("/audio/"):
            self.serve_audio(path.removeprefix("/audio/"))
            return
        if path.startswith("/static/"):
            self.serve_static(STATIC_DIR / path.removeprefix("/static/"))
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "资源不存在。"})

    def do_POST(self) -> None:
        """处理表单提交。"""
        path = urlparse(self.path).path
        if path == "/api/auth/register":
            self.handle_register()
            return
        if path == "/api/auth/login":
            self.handle_login()
            return
        if path == "/api/auth/logout":
            self.handle_logout()
            return
        if path == "/api/generate":
            self.handle_generate()
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})

    def do_PUT(self) -> None:
        """处理设置更新。"""
        path = urlparse(self.path).path
        if path == "/api/settings":
            self.handle_settings_put()
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})

    def do_DELETE(self) -> None:
        """处理历史记录删除请求。"""
        path = urlparse(self.path).path
        username = authenticate_request(self)
        if not username:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return

        if path == "/api/history":
            removed_count = clear_history_records(username)
            json_response(self, HTTPStatus.OK, {"removed": removed_count, "records": []})
            return
        if path.startswith("/api/history/"):
            record_id = unquote(path.removeprefix("/api/history/")).strip()
            if not record_id:
                json_response(self, HTTPStatus.BAD_REQUEST, {"error": "缺少历史记录 ID。"})
                return
            if not delete_history_record(username, record_id):
                json_response(self, HTTPStatus.NOT_FOUND, {"error": "历史记录不存在或无权访问。"})
                return
            json_response(self, HTTPStatus.OK, {"removed": 1, "records": read_history(username)})
            return
        json_response(self, HTTPStatus.NOT_FOUND, {"error": "接口不存在。"})

    # ---------------- 资源 ----------------

    def serve_static(self, path: Path, head_only: bool = False) -> None:
        """输出静态资源。"""
        if not path.exists() or not path.is_file():
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "静态资源不存在。"})
            return

        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        body = path.read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head_only:
            self.wfile.write(body)

    def serve_audio(self, rel_path: str, head_only: bool = False) -> None:
        """输出本地生成的音频文件；校验 token 与文件归属，支持 Range 请求。"""
        # 验证登录态（token 可从 Authorization 头或 ?token= 取）
        username = authenticate_request(self)
        if not username:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return

        cleaned = unquote(rel_path)
        parts = [p for p in cleaned.split("/") if p and p not in ("..", ".")]
        if len(parts) != 2:
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "音频文件不存在。"})
            return

        owner, file_name = parts[0], Path(parts[1]).name
        if owner != username:
            # 用户只能访问自己的音频
            json_response(self, HTTPStatus.FORBIDDEN, {"error": "无权访问该音频。"})
            return

        audio_path = OUTPUT_DIR / owner / file_name
        if not audio_path.exists() or audio_path.suffix.lower() not in (".mp3", ".wav"):
            json_response(self, HTTPStatus.NOT_FOUND, {"error": "音频文件不存在。"})
            return

        file_size = audio_path.stat().st_size
        content_type = "audio/mpeg" if audio_path.suffix.lower() == ".mp3" else "audio/wav"

        # 解析 Range 头
        range_header = self.headers.get("Range")
        if range_header and range_header.startswith("bytes="):
            try:
                range_spec = range_header[6:]
                start_str, end_str = range_spec.split("-", 1)
                start = int(start_str) if start_str else 0
                end = int(end_str) if end_str else file_size - 1
                start = max(0, min(start, file_size - 1))
                end = max(start, min(end, file_size - 1))
                length = end - start + 1

                self.send_response(HTTPStatus.PARTIAL_CONTENT)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Range", f"bytes {start}-{end}/{file_size}")
                self.send_header("Content-Length", str(length))
                self.send_header("Accept-Ranges", "bytes")
                self.end_headers()
                if not head_only:
                    with open(audio_path, "rb") as f:
                        f.seek(start)
                        self.wfile.write(f.read(length))
            except (ValueError, OSError):
                self.send_response(HTTPStatus.REQUESTED_RANGE_NOT_SATISFIABLE)
                self.send_header("Content-Range", f"bytes */{file_size}")
                self.end_headers()
            return

        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(file_size))
        self.send_header("Accept-Ranges", "bytes")
        self.end_headers()
        if not head_only:
            with open(audio_path, "rb") as f:
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

    # ---------------- 认证 API ----------------

    def handle_register(self) -> None:
        """注册账号：JSON {username, password} → 201 {token, username}"""
        payload = read_json_body(self)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        try:
            create_user(username, password)
        except ValueError as error:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        token = issue_token(username)
        json_response(self, HTTPStatus.CREATED, {"token": token, "username": username})

    def handle_login(self) -> None:
        """登录：JSON {username, password} → 200 {token, username}"""
        payload = read_json_body(self)
        username = str(payload.get("username") or "").strip()
        password = str(payload.get("password") or "")
        if not authenticate(username, password):
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "用户名或密码错误。"})
            return
        token = issue_token(username)
        json_response(self, HTTPStatus.OK, {"token": token, "username": username})

    def handle_logout(self) -> None:
        """登出：删除 token。"""
        token = extract_bearer_token(self)
        if token:
            revoke_token(token)
        json_response(self, HTTPStatus.OK, {"ok": True})

    def handle_me(self) -> None:
        """获取当前登录用户。"""
        username = authenticate_request(self)
        if not username:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "未登录。"})
            return
        json_response(self, HTTPStatus.OK, {"username": username})

    def handle_settings_get(self) -> None:
        """读取当前用户的设置（API Key 是否配置、脱敏预览）。"""
        username = authenticate_request(self)
        if not username:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return
        api_key = get_user_api_key(username)
        json_response(self, HTTPStatus.OK, {
            "has_api_key": bool(api_key),
            "masked_api_key": mask_api_key(api_key),
        })

    def handle_settings_put(self) -> None:
        """更新当前用户的 API Key。"""
        username = authenticate_request(self)
        if not username:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return
        payload = read_json_body(self)
        if "api_key" not in payload:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": "缺少 api_key 字段。"})
            return
        api_key = str(payload.get("api_key") or "").strip()
        try:
            set_user_api_key(username, api_key)
        except ValueError as error:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": str(error)})
            return
        json_response(self, HTTPStatus.OK, {
            "has_api_key": bool(api_key),
            "masked_api_key": mask_api_key(api_key),
        })

    # ---------------- 业务 API ----------------

    def handle_history_get(self) -> None:
        """读取当前用户的历史。"""
        username = authenticate_request(self)
        if not username:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return
        json_response(self, HTTPStatus.OK, {"records": read_history(username)})

    def handle_generate(self) -> None:
        """解析表单、生成语音并写入历史（按 owner 隔离）。"""
        username = authenticate_request(self)
        if not username:
            json_response(self, HTTPStatus.UNAUTHORIZED, {"error": "请先登录。"})
            return

        # 读取并解析 multipart/form-data
        try:
            content_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            content_length = 0
        body = self.rfile.read(content_length) if content_length > 0 else b""

        try:
            form = parse_multipart(body, self.headers.get("Content-Type", ""))
        except ValueError as error:
            json_response(self, HTTPStatus.BAD_REQUEST, {"error": f"表单解析失败：{error}"})
            return

        text = get_form_text(form, "text")
        instruction = get_form_text(form, "instruction")
        mode = get_form_text(form, "mode", "normal")
        output_format = get_form_text(form, "output_format", "wav")
        model_key = get_form_text(form, "model", "clone")
        preset_voice = get_form_text(form, "preset_voice", "mimo_default")
        optimize_text = get_form_text(form, "optimize_text", "false").lower() in ("1", "true", "on", "yes")
        # 优先使用用户保存的 API Key；其次回退到环境变量，方便临时调试
        api_key = get_user_api_key(username) or os.environ.get("MIMO_API_KEY", "")

        record = {
            "id": uuid.uuid4().hex,
            "owner": username,
            "created_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "text": text,
            "instruction": instruction,
            "mode": mode,
            "format": output_format,
            "model": model_key,
            "preset_voice": preset_voice if model_key == "preset" else "",
            "optimize_text": optimize_text if model_key == "design" else False,
            "status": "failed",
            "voice_name": "",
            "file_name": "",
            "audio_url": "",
            "error": "",
        }

        try:
            if not text and not (model_key == "design" and optimize_text):
                # design 模式开启 optimize_text 时允许 text 为空（后端会智能生成）
                raise ValueError("请输入需要合成的文本。")
            if mode not in ("normal", "stream"):
                raise ValueError("调用模式只能是普通或流式。")
            if output_format not in ("wav", "mp3"):
                raise ValueError("输出格式只能是 wav 或 mp3。")
            if model_key not in MODEL_ID_BY_KEY:
                raise ValueError("未知的模型类型。")
            if not api_key:
                raise ValueError("缺少 API Key，请先点击右上角钥匙图标保存你的 API Key。")

            voice_path: Path | None = None
            if model_key == "clone":
                voice_path, voice_name = save_uploaded_voice(form)
                record["voice_name"] = voice_name
            elif model_key == "preset":
                if preset_voice not in PRESET_VOICES:
                    raise ValueError("预置音色不在支持列表中。")
                record["voice_name"] = preset_voice
            elif model_key == "design":
                record["voice_name"] = "音色设计"

            result = generate_audio(
                api_key=api_key,
                owner=username,
                model_key=model_key,
                text=text,
                instruction=instruction,
                voice_path=voice_path,
                preset_voice=preset_voice,
                optimize_text=optimize_text,
                mode=mode,
                output_format=output_format,
            )
            record.update({
                "status": "success",
                "file_name": result["file_name"],
                "audio_url": result["audio_url"],
                "error": "",
            })
            append_history(record)
            json_response(self, HTTPStatus.OK, {"record": record})
        except Exception as error:
            record["error"] = normalize_api_error(error)
            json_response(self, HTTPStatus.BAD_REQUEST, {"record": record, "error": record["error"]})

    def log_message(self, format: str, *args: object) -> None:
        """输出精简访问日志。"""
        print(f"[{datetime.now().strftime('%H:%M:%S')}] {self.address_string()} {format % args}")


def build_parser() -> argparse.ArgumentParser:
    """构建服务启动参数。"""
    parser = argparse.ArgumentParser(description="MiMo 音色复刻本地网页服务")
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    parser.add_argument("--port", type=int, default=8787, help="监听端口，默认 8787")
    return parser


def main() -> int:
    """启动本地 HTTP 服务。"""
    args = build_parser().parse_args()
    ensure_runtime_dirs()
    migrate_existing_data()
    server = ThreadingHTTPServer((args.host, args.port), MimoRequestHandler)
    print(f"MiMo 本地语音工具已启动：http://{args.host}:{args.port}")
    print("按 Ctrl+C 停止服务。")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n服务已停止。")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
