// ==========================================================================
//  MiMo Voice Studio - 前端交互逻辑（含账号鉴权）
// ==========================================================================

const AUTH_TOKEN_KEY = "mimo:authToken";
const AUTH_USERNAME_KEY = "mimo:authUsername";
const HISTORY_STATE_KEY = "mimo:historyOpen";

// ------------------------------------------------------------------
// 鉴权门禁：未登录直接跳转到 /login
// ------------------------------------------------------------------
function getAuthToken() {
  try {
    return localStorage.getItem(AUTH_TOKEN_KEY) || "";
  } catch (e) {
    return "";
  }
}

function getAuthUsername() {
  try {
    return localStorage.getItem(AUTH_USERNAME_KEY) || "";
  } catch (e) {
    return "";
  }
}

function clearAuth() {
  try {
    localStorage.removeItem(AUTH_TOKEN_KEY);
    localStorage.removeItem(AUTH_USERNAME_KEY);
  } catch (e) {
    /* ignore */
  }
}

function redirectToLogin() {
  clearAuth();
  window.location.href = "/login";
}

// 早期门禁：无 token 直接拦截
if (!getAuthToken()) {
  redirectToLogin();
}

// 给所有受保护接口自动加 Authorization 头
async function authFetch(url, options = {}) {
  const token = getAuthToken();
  const headers = new Headers(options.headers || {});
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    redirectToLogin();
    throw new Error("登录状态已失效，正在跳转登录页。");
  }
  return response;
}

/** 给一个 audio_url 拼上 token，方便 <audio> 标签和下载链接直接使用。*/
function urlWithToken(url) {
  if (!url) {
    return url;
  }
  const token = getAuthToken();
  if (!token) {
    return url;
  }
  const sep = url.includes("?") ? "&" : "?";
  return `${url}${sep}token=${encodeURIComponent(token)}`;
}

// ------------------------------------------------------------------
// DOM 元素
// ------------------------------------------------------------------
const form = document.querySelector("#generateForm");
const textArea = document.querySelector("#text");
const charCount = document.querySelector("#charCount");
const message = document.querySelector("#message");
const generateButton = document.querySelector("#generateButton");
const refreshButton = document.querySelector("#refreshButton");
const clearHistoryButton = document.querySelector("#clearHistoryButton");
const collapseHistoryButton = document.querySelector("#collapseHistoryButton");
const expandHistoryButton = document.querySelector("#expandHistoryButton");
const historyList = document.querySelector("#historyList");
const historyCount = document.querySelector("#historyCount");
const historyToggleCount = document.querySelector("#historyToggleCount");

const voiceInput = document.querySelector("#voice");
const voiceDropzone = document.querySelector("#voiceDropzone");
const voiceDropzoneText = document.querySelector("#voiceDropzoneText");

// 模型切换 & 动态字段
const modelRadios = document.querySelectorAll('input[name="model"]');
const voiceCloneField = document.querySelector("#voiceCloneField");
const presetVoiceField = document.querySelector("#presetVoiceField");
const presetVoiceGrid = document.querySelector("#presetVoiceGrid");
const presetVoiceInput = document.querySelector("#presetVoiceInput");
const designField = document.querySelector("#designField");
const instructionInput = document.querySelector("#instruction");
const instructionLabel = document.querySelector("#instructionLabel");
const instructionOptional = document.querySelector("#instructionOptional");
const instructionHint = document.querySelector("#instructionHint");
const optimizeText = document.querySelector("#optimizeText");

// API Key 区（topbar 弹出层）
const apiKeyStatus = document.querySelector("#apiKeyStatus");
const apiKeyInput = document.querySelector("#apiKey");
const saveApiKeyButton = document.querySelector("#saveApiKeyButton");
const apiKeyHint = document.querySelector("#apiKeyHint");
const apiKeyButton = document.querySelector("#apiKeyButton");
const apiKeyPopover = document.querySelector("#apiKeyPopover");
const apiKeyDot = document.querySelector("#apiKeyDot");

// 用户信息条
const userChip = document.querySelector("#userChip");
const userAvatar = document.querySelector("#userAvatar");
const userName = document.querySelector("#userName");
const logoutButton = document.querySelector("#logoutButton");

// 预置音色列表（与后端 PRESET_VOICES 对齐）
const PRESET_VOICES = [
  { id: "mimo_default", name: "默认", tag: "MiMo" },
  { id: "冰糖", name: "冰糖", tag: "中文女" },
  { id: "茉莉", name: "茉莉", tag: "中文女" },
  { id: "苏打", name: "苏打", tag: "中文男" },
  { id: "白桦", name: "白桦", tag: "中文男" },
  { id: "Mia", name: "Mia", tag: "EN 女" },
  { id: "Chloe", name: "Chloe", tag: "EN 女" },
  { id: "Milo", name: "Milo", tag: "EN 男" },
  { id: "Dean", name: "Dean", tag: "EN 男" },
];

// 不同模型对应的文案
const INSTRUCTION_PRESETS = {
  clone: {
    label: "语气 / 风格",
    optional: "可选",
    placeholder: "例如：温柔、自然、语速稍慢",
    hint: "",
  },
  preset: {
    label: "语气 / 风格",
    optional: "可选",
    placeholder: "例如：(开心)、温柔治愈、深夜电台 DJ",
    hint: "可以填风格描述，也可在合成文本前加 (风格) 标签直接控制。",
  },
  design: {
    label: "音色描述",
    optional: "必填",
    placeholder: "例如：年迈的老先生，带北方口音，语速沉稳，嗓音略带沙哑沧桑",
    hint: "用 1-4 句话从性别年龄、音色质感、情绪语速等维度描述目标音色。",
  },
};

// 最新结果区元素
const latestResult = document.querySelector("#latestResult");
const resultAudio = document.querySelector("#resultAudio");
const resultPlayer = document.querySelector("#resultPlayer");
const resultDownload = document.querySelector("#resultDownload");
const resultMeta = document.querySelector("#resultMeta");
const resultText = document.querySelector("#resultText");
const resultBadge = latestResult.querySelector(".result__badge");
const resultHeading = latestResult.querySelector(".result__heading");

// ------------------------------------------------------------------
// 工具：消息提示 / HTML 转义
// ------------------------------------------------------------------
let messageHideTimer = null;

function showMessage(text, type = "info", autoHide = true) {
  if (messageHideTimer) {
    clearTimeout(messageHideTimer);
    messageHideTimer = null;
  }
  message.hidden = false;
  message.textContent = text;
  message.className = `message ${type}`;
  if (autoHide && type !== "error") {
    messageHideTimer = setTimeout(clearMessage, 4500);
  }
}

function clearMessage() {
  message.hidden = true;
  message.textContent = "";
  message.className = "message";
}

function escapeHtml(value) {
  return String(value || "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function modeText(mode) {
  return mode === "stream" ? "流式兼容" : "普通";
}

function modelText(model) {
  if (model === "preset") return "预置音色";
  if (model === "design") return "音色设计";
  return "音色克隆";
}

// ------------------------------------------------------------------
// 自定义播放器绑定
// ------------------------------------------------------------------
function fmtTime(s) {
  if (!s || !isFinite(s)) return "0:00";
  const m = Math.floor(s / 60);
  const sec = Math.floor(s % 60);
  return `${m}:${sec < 10 ? "0" : ""}${sec}`;
}

function bindPlayer(playerEl, audioEl) {
  if (!playerEl || !audioEl || playerEl.dataset.bound) return;
  playerEl.dataset.bound = "1";
  playerEl.hidden = false;

  const btnPlay = playerEl.querySelector(".player__play");
  const iconPlay = playerEl.querySelector(".player__icon-play");
  const iconPause = playerEl.querySelector(".player__icon-pause");
  const timeCur = playerEl.querySelector(".player__time--cur");
  const timeDur = playerEl.querySelector(".player__time--dur");
  const track = playerEl.querySelector(".player__track");
  const fill = playerEl.querySelector(".player__fill");
  const thumb = playerEl.querySelector(".player__thumb");
  const muteBtn = playerEl.querySelector(".player__mute");
  const volTrack = playerEl.querySelector(".player__vol-track");
  const volFill = playerEl.querySelector(".player__vol-fill");

  function showPlaying(v) {
    iconPlay.hidden = v;
    iconPause.hidden = !v;
  }

  btnPlay.addEventListener("click", () => {
    if (audioEl.paused) { audioEl.play(); } else { audioEl.pause(); }
  });

  audioEl.addEventListener("play", () => showPlaying(true));
  audioEl.addEventListener("pause", () => showPlaying(false));
  audioEl.addEventListener("ended", () => { showPlaying(false); fill.style.width = "0%"; thumb.style.left = "0%"; });

  audioEl.addEventListener("timeupdate", () => {
    if (!audioEl.duration || !isFinite(audioEl.duration)) return;
    const pct = (audioEl.currentTime / audioEl.duration) * 100;
    fill.style.width = pct + "%";
    thumb.style.left = pct + "%";
    timeCur.textContent = fmtTime(audioEl.currentTime);
  });

  audioEl.addEventListener("loadedmetadata", () => {
    timeDur.textContent = fmtTime(audioEl.duration);
  });

  // 若浏览器直接给了 duration（缓存命中）
  if (audioEl.duration && isFinite(audioEl.duration)) {
    timeDur.textContent = fmtTime(audioEl.duration);
  }

  // 进度条点击
  track.addEventListener("click", (e) => {
    if (!audioEl.duration || !isFinite(audioEl.duration)) return;
    const rect = track.getBoundingClientRect();
    const pct = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
    audioEl.currentTime = pct * audioEl.duration;
  });

  // 音量
  if (muteBtn) {
    audioEl.volume = 1;
    muteBtn.addEventListener("click", () => {
      audioEl.muted = !audioEl.muted;
      muteBtn.classList.toggle("is-muted", audioEl.muted);
      if (volFill) volFill.style.width = audioEl.muted ? "0%" : (audioEl.volume * 100) + "%";
    });
  }
  if (volTrack) {
    volTrack.addEventListener("click", (e) => {
      const rect = volTrack.getBoundingClientRect();
      const v = Math.max(0, Math.min(1, (e.clientX - rect.left) / rect.width));
      audioEl.volume = v;
      audioEl.muted = v === 0;
      volFill.style.width = (v * 100) + "%";
      if (muteBtn) muteBtn.classList.toggle("is-muted", v === 0);
    });
  }
}

function bindAllPlayers() {
  document.querySelectorAll(".player[data-src]").forEach((el) => {
    const src = el.dataset.src;
    // 同级 audio 元素
    const audioEl = el.previousElementSibling;
    if (audioEl && audioEl.tagName === "AUDIO") {
      audioEl.src = src;
      bindPlayer(el, audioEl);
    }
  });
}

// ------------------------------------------------------------------
// 字数实时统计
// ------------------------------------------------------------------
function updateCharCount() {
  charCount.textContent = textArea.value.length;
}

// ------------------------------------------------------------------
// 音色样本：拖拽 + 文件名展示
// ------------------------------------------------------------------
function setVoiceFileLabel(file) {
  if (!file) {
    voiceDropzone.classList.remove("has-file");
    voiceDropzoneText.textContent = "点击上传 / 拖拽 WAV / MP3";
    return;
  }
  voiceDropzone.classList.add("has-file");
  const sizeKb = (file.size / 1024).toFixed(1);
  voiceDropzoneText.textContent = `${file.name} · ${sizeKb} KB`;
}

function bindDropzone() {
  voiceInput.addEventListener("change", (e) => {
    const file = e.target.files && e.target.files[0];
    setVoiceFileLabel(file);
  });

  ["dragenter", "dragover"].forEach((evt) => {
    voiceDropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      voiceDropzone.classList.add("is-dragging");
    });
  });

  ["dragleave", "drop"].forEach((evt) => {
    voiceDropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      e.stopPropagation();
      voiceDropzone.classList.remove("is-dragging");
    });
  });

  voiceDropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files[0];
    if (!file) {
      return;
    }
    const allowedExt = [".wav", ".mp3"];
    const lower = file.name.toLowerCase();
    const okExt = allowedExt.some((ext) => lower.endsWith(ext));
    if (!okExt) {
      showMessage("仅支持 .wav 或 .mp3 文件。", "error");
      return;
    }
    const dt = new DataTransfer();
    dt.items.add(file);
    voiceInput.files = dt.files;
    setVoiceFileLabel(file);
  });
}

// ------------------------------------------------------------------
// 模型切换 + 动态字段
// ------------------------------------------------------------------
function getSelectedModel() {
  const checked = Array.from(modelRadios).find((r) => r.checked);
  return (checked && checked.value) || "preset";
}

function renderPresetVoices() {
  presetVoiceGrid.innerHTML = PRESET_VOICES.map(
    (v) => `
      <button type="button" class="voice-tile" data-voice-id="${escapeHtml(v.id)}">
        <span class="voice-tile__name">${escapeHtml(v.name)}</span>
        <span class="voice-tile__tag">${escapeHtml(v.tag)}</span>
      </button>
    `
  ).join("");

  presetVoiceGrid.addEventListener("click", (event) => {
    const tile = event.target.closest(".voice-tile");
    if (!tile) {
      return;
    }
    selectPresetVoice(tile.dataset.voiceId);
  });

  selectPresetVoice(presetVoiceInput.value || "mimo_default");
}

function selectPresetVoice(voiceId) {
  presetVoiceInput.value = voiceId;
  presetVoiceGrid.querySelectorAll(".voice-tile").forEach((el) => {
    el.classList.toggle("is-active", el.dataset.voiceId === voiceId);
  });
}

function applyModelMode(model) {
  // 切换字段显隐
  voiceCloneField.hidden = model !== "clone";
  presetVoiceField.hidden = model !== "preset";
  designField.hidden = model !== "design";

  // 更新指令字段文案
  const preset = INSTRUCTION_PRESETS[model] || INSTRUCTION_PRESETS.clone;
  instructionLabel.textContent = preset.label;
  instructionOptional.textContent = preset.optional;
  instructionOptional.classList.toggle("field__optional--required", preset.optional === "必填");
  instructionInput.placeholder = preset.placeholder;
  if (preset.hint) {
    instructionHint.textContent = preset.hint;
    instructionHint.hidden = false;
  } else {
    instructionHint.hidden = true;
  }
}

function bindModelSwitch() {
  modelRadios.forEach((radio) => {
    radio.addEventListener("change", () => {
      if (radio.checked) {
        applyModelMode(radio.value);
      }
    });
  });
  applyModelMode(getSelectedModel());
}

// ------------------------------------------------------------------
// API Key 加载 / 保存
// ------------------------------------------------------------------
function renderApiKeyStatus(hasKey, masked) {
  if (hasKey) {
    apiKeyStatus.textContent = `已保存 ${masked || ""}`.trim();
    apiKeyStatus.className = "apikey-popover__status is-saved";
    apiKeyDot.className = "apikey-btn__dot is-saved";
    apiKeyInput.placeholder = "留空保留原 Key；输入新值可替换";
    apiKeyHint.textContent = "已保存的 Key 仅本账号可用，不会出现在历史记录里。";
  } else {
    apiKeyStatus.textContent = "未配置";
    apiKeyStatus.className = "apikey-popover__status is-missing";
    apiKeyDot.className = "apikey-btn__dot is-missing";
    apiKeyInput.placeholder = "粘贴 MiMo API Key（保存后绑定到当前账号）";
    apiKeyHint.textContent = "首次使用请先粘贴你的 MiMo API Key 并保存，之后无需重复配置。";
  }
}

async function loadApiKeyStatus() {
  try {
    const response = await authFetch("/api/settings");
    const data = await response.json();
    renderApiKeyStatus(!!data.has_api_key, data.masked_api_key || "");
  } catch (error) {
    apiKeyStatus.textContent = "读取失败";
    apiKeyStatus.className = "apikey-popover__status is-missing";
  }
}

async function saveApiKey() {
  const value = apiKeyInput.value.trim();
  if (!value) {
    showMessage("请先粘贴 API Key 再保存。", "error");
    apiKeyInput.focus();
    return;
  }
  saveApiKeyButton.disabled = true;
  const originalLabel = saveApiKeyButton.innerHTML;
  saveApiKeyButton.textContent = "保存中…";
  try {
    const response = await authFetch("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ api_key: value }),
    });
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || "保存失败");
    }
    renderApiKeyStatus(!!data.has_api_key, data.masked_api_key || "");
    apiKeyInput.value = "";
    showMessage("API Key 已保存。", "success");
  } catch (error) {
    showMessage(error.message, "error");
  } finally {
    saveApiKeyButton.disabled = false;
    saveApiKeyButton.innerHTML = originalLabel;
  }
}

// ------------------------------------------------------------------
// 历史记录渲染
// ------------------------------------------------------------------
function renderRecord(record) {
  const isSuccess = record.status === "success";
  const tokenized = urlWithToken(record.audio_url || "");
  const audio = isSuccess
    ? `<audio preload="metadata"></audio>
       <div class="player" data-src="${escapeHtml(tokenized)}">
         <button class="player__play" type="button" aria-label="播放">
           <svg class="player__icon-play" viewBox="0 0 24 24" fill="currentColor"><polygon points="6 3 20 12 6 21 6 3"/></svg>
           <svg class="player__icon-pause" viewBox="0 0 24 24" fill="currentColor" hidden><rect x="5" y="3" width="4" height="18"/><rect x="15" y="3" width="4" height="18"/></svg>
         </button>
         <span class="player__time player__time--cur">0:00</span>
         <div class="player__track"><div class="player__fill"></div><div class="player__thumb"></div></div>
         <span class="player__time player__time--dur">0:00</span>
       </div>
       <a class="download" href="${escapeHtml(tokenized)}" download="${escapeHtml(record.file_name)}">下载</a>`
    : `<p class="error-text">${escapeHtml(record.error || "生成失败")}</p>`;

  const metaChips = [
    `<span class="meta__chip meta__chip--accent">${escapeHtml(modelText(record.model || "clone"))}</span>`,
    `<span class="meta__chip">${escapeHtml(modeText(record.mode))}</span>`,
    `<span class="meta__chip">${escapeHtml((record.format || "wav").toUpperCase())}</span>`,
    `<span class="meta__chip">${escapeHtml(record.voice_name || "默认音色")}</span>`,
  ].join("");

  const badgeClass = isSuccess ? "badge--success" : "badge--failed";
  const badgeText = isSuccess ? "成功" : "失败";

  return `
    <article class="record ${isSuccess ? "success" : "failed"}">
      <div class="record-main">
        <div class="record-body">
          <p class="record-time">${escapeHtml(record.created_at || "")}</p>
          <h3>${escapeHtml(record.text || "")}</h3>
          <div class="meta">${metaChips}</div>
          ${record.instruction ? `<p class="instruction">${escapeHtml(record.instruction)}</p>` : ""}
        </div>
        <div class="record-actions">
          <span class="badge ${badgeClass}">${badgeText}</span>
          <button class="delete-record" type="button" data-record-id="${escapeHtml(record.id)}">删除</button>
        </div>
      </div>
      <div class="audio-line">${audio}</div>
    </article>
  `;
}

function setHistoryCount(n) {
  historyCount.textContent = `${n} 条`;
  historyToggleCount.textContent = String(n);
}

async function loadHistory() {
  try {
    const response = await authFetch("/api/history");
    const data = await response.json();
    const records = data.records || [];
    setHistoryCount(records.length);
    historyList.innerHTML = records.length
      ? records.map(renderRecord).join("")
      : `<div class="empty">还没有生成记录，先来合成第一段语音吧 ✨</div>`;
    bindAllPlayers();
    return records;
  } catch (error) {
    showMessage(`加载历史失败：${error.message}`, "error");
    return [];
  }
}

// ------------------------------------------------------------------
// 最新结果区
// ------------------------------------------------------------------

function showLatestSuccess(record) {
  latestResult.hidden = false;
  latestResult.classList.remove("is-failed");

  resultBadge.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <polyline points="20 6 9 17 4 12" />
    </svg>
  `;
  resultHeading.textContent = "生成成功";

  const meta = [
    record.created_at || "",
    modelText(record.model || "clone"),
    modeText(record.mode || ""),
    (record.format || "wav").toUpperCase(),
    record.voice_name || "默认音色",
    record.file_name || "",
  ].filter(Boolean).join(" · ");
  resultMeta.textContent = meta;

  const tokenized = urlWithToken(record.audio_url || "");
  resultAudio.src = tokenized;
  // 重置并绑定自定义播放器
  delete resultPlayer.dataset.bound;
  bindPlayer(resultPlayer, resultAudio);
  resultDownload.hidden = false;
  resultDownload.href = tokenized;
  resultDownload.download = record.file_name || "";

  resultText.textContent = record.text || "";
  resultText.hidden = !record.text;

  latestResult.style.animation = "none";
  void latestResult.offsetWidth;
  latestResult.style.animation = "";

  latestResult.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

function showLatestFailure(errorText, record) {
  latestResult.hidden = false;
  latestResult.classList.add("is-failed");

  resultBadge.innerHTML = `
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
      <line x1="18" y1="6" x2="6" y2="18" />
      <line x1="6" y1="6" x2="18" y2="18" />
    </svg>
  `;
  resultHeading.textContent = "生成失败";

  const meta = record && record.created_at ? `${record.created_at} · ${modeText(record.mode || "")}` : "";
  resultMeta.textContent = meta;

  resultAudio.removeAttribute("src");
  resultPlayer.hidden = true;
  resultDownload.hidden = true;

  resultText.textContent = errorText || "生成失败";
  resultText.hidden = false;

  latestResult.style.animation = "none";
  void latestResult.offsetWidth;
  latestResult.style.animation = "";
}

// ------------------------------------------------------------------
// 生成语音
// ------------------------------------------------------------------
function setGenerating(loading) {
  if (loading) {
    generateButton.disabled = true;
    generateButton.classList.add("is-loading");
  } else {
    generateButton.disabled = false;
    generateButton.classList.remove("is-loading");
  }
}

async function submitGenerate(event) {
  event.preventDefault();
  clearMessage();

  const model = getSelectedModel();
  const text = textArea.value.trim();
  const instruction = instructionInput.value.trim();
  const optimizeOn = !!(optimizeText && optimizeText.checked);

  // 文本校验：design + optimize_text 开启时可以为空，其他必填
  if (!text && !(model === "design" && optimizeOn)) {
    showMessage("请先输入要合成的文本。", "error");
    textArea.focus();
    return;
  }
  if (model === "design" && !instruction) {
    showMessage("音色设计模式必须填写音色描述。", "error");
    instructionInput.focus();
    return;
  }

  setGenerating(true);
  try {
    const formData = new FormData(form);
    // FormData 已带 model / preset_voice / optimize_text / voice 等字段；
    // 移除掉非当前模型的字段，避免多余数据干扰
    if (model !== "clone") {
      formData.delete("voice");
    }
    if (model !== "preset") {
      formData.delete("preset_voice");
    }
    if (model !== "design") {
      formData.delete("optimize_text");
    }

    const response = await authFetch("/api/generate", {
      method: "POST",
      body: formData,
    });
    const data = await response.json();
    if (!response.ok) {
      showLatestFailure(data.error || "生成失败", data.record);
      throw new Error(data.error || "生成失败");
    }
    showMessage(`已生成：${data.record.file_name}`, "success");
    showLatestSuccess(data.record);
    await loadHistory();
  } catch (error) {
    showMessage(error.message, "error");
    await loadHistory();
  } finally {
    setGenerating(false);
  }
}

// ------------------------------------------------------------------
// 删除单条 / 清空全部
// ------------------------------------------------------------------
async function deleteRecord(recordId) {
  if (!recordId) {
    return;
  }
  const confirmed = window.confirm("确定删除这条历史记录吗？对应的本地音频文件也会一起删除。");
  if (!confirmed) {
    return;
  }
  const response = await authFetch(`/api/history/${encodeURIComponent(recordId)}`, { method: "DELETE" });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "删除失败");
  }
  showMessage("历史记录已删除。", "success");
  await loadHistory();
}

async function clearHistory() {
  const confirmed = window.confirm("确定清空全部历史记录吗？对应的本地音频文件也会一起删除。");
  if (!confirmed) {
    return;
  }
  const response = await authFetch("/api/history", { method: "DELETE" });
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || "清空失败");
  }
  showMessage(`已清空 ${data.removed || 0} 条历史记录。`, "success");
  await loadHistory();
}

// ------------------------------------------------------------------
// 用户信息条 + 退出登录
// ------------------------------------------------------------------
function renderUserChip(username) {
  if (!username) {
    return;
  }
  userChip.hidden = false;
  logoutButton.hidden = false;
  userName.textContent = username;
  userAvatar.textContent = username.charAt(0).toUpperCase();
}

async function handleLogout() {
  try {
    await authFetch("/api/auth/logout", { method: "POST" });
  } catch (e) {
    /* 即便后端失败也清本地 */
  }
  clearAuth();
  window.location.href = "/login";
}

async function verifyAuthAndInit() {
  try {
    const response = await authFetch("/api/auth/me");
    const data = await response.json();
    if (!data.username) {
      throw new Error("未登录。");
    }
    // 同步 username（防止 localStorage 与 token 关联的用户名不一致）
    try {
      localStorage.setItem(AUTH_USERNAME_KEY, data.username);
    } catch (e) {
      /* ignore */
    }
    renderUserChip(data.username);
    return true;
  } catch (error) {
    // 401 已经 redirect 了；其他错误也按未登录处理
    return false;
  }
}

// ------------------------------------------------------------------
// 历史折叠 / 展开
// ------------------------------------------------------------------
function setHistoryOpen(open) {
  document.body.setAttribute("data-history", open ? "open" : "closed");
  try {
    localStorage.setItem(HISTORY_STATE_KEY, open ? "1" : "0");
  } catch (e) {
    /* ignore */
  }
}

function initHistoryState() {
  let saved = null;
  try {
    saved = localStorage.getItem(HISTORY_STATE_KEY);
  } catch (e) {
    saved = null;
  }
  setHistoryOpen(saved !== "0");
}

// ------------------------------------------------------------------
// 键盘快捷键：Cmd/Ctrl + Enter 提交
// ------------------------------------------------------------------
function bindShortcuts() {
  textArea.addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") {
      e.preventDefault();
      form.requestSubmit();
    }
  });
}

// ------------------------------------------------------------------
// 初始化
// ------------------------------------------------------------------
form.addEventListener("submit", submitGenerate);
refreshButton.addEventListener("click", () => {
  loadHistory().then(() => showMessage("历史已刷新。", "info"));
});
clearHistoryButton.addEventListener("click", () => {
  clearHistory().catch((error) => showMessage(error.message, "error"));
});
collapseHistoryButton.addEventListener("click", () => setHistoryOpen(false));
expandHistoryButton.addEventListener("click", () => setHistoryOpen(true));
logoutButton.addEventListener("click", () => {
  handleLogout();
});

historyList.addEventListener("click", (event) => {
  const target = event.target.closest(".delete-record");
  if (!target) {
    return;
  }
  deleteRecord(target.dataset.recordId).catch((error) => showMessage(error.message, "error"));
});

textArea.addEventListener("input", updateCharCount);
updateCharCount();
bindDropzone();
bindShortcuts();
initHistoryState();

// 渲染预置音色 + 绑定模型切换
renderPresetVoices();
bindModelSwitch();

// API Key popover 开关
apiKeyButton.addEventListener("click", (e) => {
  e.stopPropagation();
  apiKeyPopover.hidden = !apiKeyPopover.hidden;
});
document.addEventListener("click", (e) => {
  if (!apiKeyPopover.hidden && !apiKeyPopover.contains(e.target) && e.target !== apiKeyButton) {
    apiKeyPopover.hidden = true;
  }
});

// API Key 保存按钮
saveApiKeyButton.addEventListener("click", () => {
  saveApiKey();
});
apiKeyInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") {
    e.preventDefault();
    saveApiKey();
  }
});

// 验证登录 → 加载 API Key 状态 + 历史
verifyAuthAndInit().then((ok) => {
  if (ok) {
    loadApiKeyStatus();
    loadHistory().catch((error) => showMessage(error.message, "error"));
  }
});
