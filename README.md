# MiMo TTS · 本地语音克隆与合成工作站

> 基于小米 MiMo-V2.5-TTS 系列模型打造的本地 Web 工作站，支持**预置音色 / 文本设计音色 / 上传样本克隆**三种合成方式，自带账号系统与现代化暗色界面。

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?logo=python&logoColor=white" />
  <img src="https://img.shields.io/badge/Frontend-Vanilla%20JS-F7DF1E?logo=javascript&logoColor=black" />
  <img src="https://img.shields.io/badge/Model-MiMo--V2.5--TTS-8b5cf6" />
  <img src="https://img.shields.io/badge/License-MIT-06b6d4" />
</p>

---

## ✨ 核心特性

### 🎭 三种合成模式自由切换

| 模式 | 模型 ID | 特点 |
|---|---|---|
| **预置音色** | `mimo-v2.5-tts` | 9 款官方精品音色（中文男女 + 英文男女）开箱即用，支持唱歌模式 |
| **音色设计** | `mimo-v2.5-tts-voicedesign` | 用一段自然语言描述生成定制音色，可开启「智能润色」让模型自动生成示范文本 |
| **音色克隆** | `mimo-v2.5-tts-voiceclone` | 上传 WAV/MP3 样本精准复刻任意音色 |

支持「自然语言风格控制」与「音频标签控制」两种风格指令方式，全面对接官方文档能力。

### 👥 多账号隔离

- 启动后自动跳转到登录/注册页
- 密码用 **PBKDF2-SHA256 + 16-byte salt** 哈希存储
- 每个账号独立：历史记录、生成文件目录、API Key
- 音频 URL 加 Bearer Token 校验，跨账号访问直接 403
- 首次启动需通过「注册」创建账号

### 🔑 API Key 持久化

每个账号配置一次 MiMo API Key 后**自动绑定到当前账号**，后续登录无需重复输入，随时可点击右上角钥匙图标修改。

### 🎨 现代化深色界面

- 极光渐变背景 + 玻璃磨砂卡片 + 紫青渐变 CTA
- 字体：Outfit（标题）+ Work Sans（正文）+ JetBrains Mono（数字）
- 主功能在 1440×900 一屏内呈现，历史侧栏可折叠
- 完整响应式：1440 / 1024 / 768 / 375 自适应
- 适配 `prefers-reduced-motion`

### ⚡ 实用细节

- 字数实时统计，`⌘/Ctrl + Enter` 快速提交
- 拖拽上传音色样本
- 生成中按钮变声波动画
- 最新结果区直接在主面板下方展示（不必跳到右侧历史）
- WAV 与 MP3 双格式输出（MP3 需本地 `ffmpeg`）
- 普通 / 流式兼容两种调用模式

---

## 🚀 快速开始

### 1. 准备环境

- **Python ≥ 3.10**（标准库 `http.server`，无第三方依赖）
- **ffmpeg**（可选，用于 WAV → MP3 转换）

```bash
brew install ffmpeg            # macOS
sudo apt install ffmpeg        # Debian/Ubuntu
```

### 2. 获取 MiMo API Key

到 [小米 MiMo 开放平台](https://platform.xiaomimimo.com/) 注册并获取 API Key（首次注册有免费额度）。

### 3. 启动服务

```bash
git clone https://github.com/EmingChao/MiMo-TTS.git
cd MiMo-TTS
python3 server.py
```

默认监听 `http://127.0.0.1:8787`，自定义端口：

```bash
python3 server.py --host 0.0.0.0 --port 9000
```

### 4. 首次使用

1. 浏览器打开 [http://127.0.0.1:8787](http://127.0.0.1:8787)
2. 点「注册」创建新账号并登录
3. 点击右上角钥匙图标，粘贴你的 MiMo API Key 并保存
4. 选择模型 → 输入文本 → 点「生成语音」

> 💡 音色克隆模式需要上传 WAV/MP3 音频样本。

---

## 📂 项目结构

```
MiMo-TTS/
├── server.py                       # 单文件 HTTP 服务：路由、认证、模型调用
├── static/
│   ├── index.html                  # 主工作台（生成 + 历史）
│   ├── login.html                  # 登录 / 注册页
│   ├── app.js                      # 主页交互逻辑
│   ├── login.js                    # 登录页逻辑
│   └── styles.css                  # 极光玻璃拟态设计系统
├── scripts/
│   └── mimo_voiceclone_tts.py      # 命令行调用样例（保留供参考）
├── docs/
│   └── local-voiceclone-web-plan.md
├── data/                           # 运行时生成（已 gitignore）
│   ├── users.json                  # 用户表（含密码哈希 + API Key）
│   ├── tokens.json                 # Session token
│   ├── history.json                # 全部账号历史记录（按 owner 隔离查询）
│   └── voices/                     # 用户上传的音色样本缓存
├── output/                         # 模型生成的音频（按用户名分目录，已 gitignore）
│   └── <username>/
└── .gitignore
```

---

## 🔌 API 概览

所有业务接口需带 `Authorization: Bearer <token>`，音频资源同时支持 `?token=` 查询参数（方便 `<audio>` 标签直接播放）。

| Method | Endpoint | 说明 |
|---|---|---|
| `POST` | `/api/auth/register` | 注册账号 `{username, password}` |
| `POST` | `/api/auth/login` | 登录返回 `{token, username}` |
| `POST` | `/api/auth/logout` | 注销当前 token |
| `GET`  | `/api/auth/me` | 获取当前登录用户 |
| `GET`  | `/api/settings` | 查看 API Key 状态（脱敏） |
| `PUT`  | `/api/settings` | 更新当前用户的 API Key |
| `POST` | `/api/generate` | 合成语音（multipart 表单） |
| `GET`  | `/api/history` | 当前用户的历史记录 |
| `DELETE` | `/api/history` | 清空当前用户历史 |
| `DELETE` | `/api/history/<id>` | 删除单条 |
| `GET`  | `/audio/<username>/<file>` | 音频文件（跨账号访问 403） |

---

## 🛡️ 安全说明

| 项 | 处理方式 |
|---|---|
| 密码 | PBKDF2-SHA256 + 16-byte salt + 200,000 次迭代 |
| 会话 | `secrets.token_urlsafe(32)` 生成，落盘 `data/tokens.json`，服务重启不掉线 |
| API Key | 写入 `data/users.json`，前端只展示脱敏 `sk-***xxxx` |
| 音频访问 | URL 内嵌用户名，请求时校验「token 所属用户 == URL 用户」，不匹配返回 403 |
| 跨用户隔离 | 历史按 `owner` 字段过滤、音频按子目录分离、生成时强制覆盖 owner 字段 |
| 文件名 | `safe_original_name()` 移除路径分隔符避免穿越 |

⚠️ 本项目设计为**本地单机使用**。如果要部署到公网请：
- 启用 HTTPS（反向代理 + 证书）
- 增加 token 过期机制
- 加上请求频率限制
- 限制注册（设置邀请码等）

---

## 🎨 设计系统

- 调色板：`#0F0F23` 深紫底 / `#8B5CF6 → #06B6D4` 紫青渐变 / `#10B981` 成功 / `#F87171` 失败
- 字体：[Outfit](https://fonts.google.com/specimen/Outfit) + [Work Sans](https://fonts.google.com/specimen/Work+Sans) + [JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono)
- 动效：3 个极光光斑漂移（22-30s）、按钮 hover 渐变扫光、音频生成时声波动画

---

## 📜 License

MIT

---

## 🙏 鸣谢

- [小米 MiMo 开放平台](https://platform.xiaomimimo.com/) — 提供 MiMo-V2.5-TTS 系列模型
- 字体：[Outfit](https://fonts.google.com/specimen/Outfit)、[Work Sans](https://fonts.google.com/specimen/Work+Sans)、[JetBrains Mono](https://fonts.google.com/specimen/JetBrains+Mono)
- 图标：[Lucide](https://lucide.dev/) 风格 SVG（手写）
