# MiMo 音色复刻本地网页工具计划

## 目标

在 `/Users/liumingchao/develop/private/mimo_test` 中提供一个本地网页工具，用于输入文本、上传克隆音色样本、调用小米 `mimo-v2.5-tts-voiceclone` 模型生成语音，并把结果保存到本地 `output` 目录。

## 功能范围

1. 文本输入：用户在网页中输入需要合成的文本。
2. 风格指令：可选输入语气、情绪、语速等自然语言控制内容。
3. 调用模式：支持普通模式和流式兼容模式。
4. 音色样本：支持上传 `wav` 或 `mp3` 文件；不上传时默认使用本地 `诺艾尔.wav`。
5. 输出格式：默认保存为 `wav`，文件名格式为 `yyyyMMdd_HHmmss_随机串.wav`；如需 `mp3` 可在页面中选择。
6. 本地缓存：生成记录保存到 `data/history.json`，上传样本保存到 `data/voices`。
7. 历史记录：网页中展示历史文本、模式、音色样本、输出文件、状态和错误信息。

## 技术方案

- 后端：Python 标准库 `http.server`，不引入 Flask 等额外依赖。
- 前端：原生 HTML/CSS/JavaScript，无构建步骤。
- 接口调用：使用 `urllib.request` 调用 `https://api.xiaomimimo.com/v1/chat/completions`。
- 音频处理：普通模式指定 `wav` 时直接保存接口返回的 wav Base64 音频；流式兼容模式接收 `pcm16` 后封装为 wav；仅在页面选择 `mp3` 时使用本机 `ffmpeg` 转换。
- API Key：优先读取环境变量 `MIMO_API_KEY`，网页也允许临时输入；不会写入历史记录。

## 关键约束

1. 官方文档说明 MiMo-V2.5-TTS 的流式低延迟暂未上线，当前流式只是兼容模式，实际仍会在推理完成后返回一次结果。
2. 普通模式返回 JSON，音频内容在 `choices[0].message.audio.data`，是 Base64 编码的音频字节；不是音频 URL。
3. 流式模式返回 SSE 事件，音频内容在 `choices[0].delta.audio.data`，文档建议格式为 `pcm16`，需本地封装为 wav 后播放。
4. 音色复刻样本 Base64 字符串大小不能超过 10 MB，目前仅支持 `mp3` 和 `wav`。
5. 如果账户余额不足，接口会返回 `HTTP 402 insufficient_balance`，页面需要展示错误并记录失败历史。
6. 本地默认端口使用 `8787`，如果被占用可通过命令行参数换端口。

## 验证方式

1. 启动服务后用 Chrome 打开 `http://127.0.0.1:8787`。
2. 检查页面加载、历史记录接口、默认音色显示。
3. 使用当前 API Key 调用一次，预期如果余额不足则页面显示明确错误；如果余额充足则 `output` 目录生成 wav 或 mp3。
