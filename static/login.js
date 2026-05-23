// ==========================================================================
//  登录 / 注册页面交互
// ==========================================================================

const AUTH_TOKEN_KEY = "mimo:authToken";
const AUTH_USERNAME_KEY = "mimo:authUsername";

const tabs = document.querySelectorAll(".auth-tab");
const form = document.querySelector("#authForm");
const usernameInput = document.querySelector("#username");
const passwordInput = document.querySelector("#password");
const submitButton = document.querySelector("#authSubmit");
const submitText = document.querySelector("#authSubmitText");
const message = document.querySelector("#authMessage");
const formTitle = document.querySelector("#formTitle");
const formHint = document.querySelector("#formHint");

let currentTab = "login"; // "login" | "register"

function showMessage(text, type = "error") {
  message.hidden = false;
  message.textContent = text;
  message.className = `message ${type}`;
}

function clearMessage() {
  message.hidden = true;
  message.textContent = "";
  message.className = "message";
}

function switchTab(tab) {
  if (tab === currentTab) {
    return;
  }
  currentTab = tab;
  tabs.forEach((el) => {
    el.classList.toggle("is-active", el.dataset.tab === tab);
  });
  if (tab === "login") {
    formTitle.textContent = "欢迎回来";
    formHint.textContent = "登录后开始你的语音克隆之旅";
    submitText.textContent = "登录";
    passwordInput.autocomplete = "current-password";
  } else {
    formTitle.textContent = "创建账号";
    formHint.textContent = "注册一个属于你的工作空间";
    submitText.textContent = "注册并登录";
    passwordInput.autocomplete = "new-password";
  }
  clearMessage();
}

tabs.forEach((el) => {
  el.addEventListener("click", () => switchTab(el.dataset.tab));
});

function setLoading(loading) {
  submitButton.disabled = loading;
  submitButton.classList.toggle("is-loading", loading);
}

async function handleSubmit(event) {
  event.preventDefault();
  clearMessage();

  const username = usernameInput.value.trim();
  const password = passwordInput.value;

  if (!username || !password) {
    showMessage("请填写用户名和密码。");
    return;
  }

  const endpoint = currentTab === "register" ? "/api/auth/register" : "/api/auth/login";
  setLoading(true);
  try {
    const response = await fetch(endpoint, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
      throw new Error(data.error || "操作失败，请稍后再试。");
    }
    if (!data.token || !data.username) {
      throw new Error("服务端返回数据异常。");
    }
    try {
      localStorage.setItem(AUTH_TOKEN_KEY, data.token);
      localStorage.setItem(AUTH_USERNAME_KEY, data.username);
    } catch (e) {
      /* localStorage 不可用时忽略 */
    }
    // 跳回主页
    window.location.href = "/";
  } catch (error) {
    showMessage(error.message || "操作失败。");
  } finally {
    setLoading(false);
  }
}

form.addEventListener("submit", handleSubmit);

// 如果已经登录，直接跳主页
(function autoSkipIfLoggedIn() {
  let token = null;
  try {
    token = localStorage.getItem(AUTH_TOKEN_KEY);
  } catch (e) {
    /* ignore */
  }
  if (!token) {
    return;
  }
  fetch("/api/auth/me", { headers: { Authorization: `Bearer ${token}` } })
    .then((resp) => (resp.ok ? resp.json() : null))
    .then((data) => {
      if (data && data.username) {
        window.location.href = "/";
      }
    })
    .catch(() => {
      /* token 失效则保持登录页 */
    });
})();
