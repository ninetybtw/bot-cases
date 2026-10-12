// Клиент API. В Telegram подписывает запросы initData, в демо-режиме ходит в MockApi.
(function () {
  const tg = () => (window.Telegram && window.Telegram.WebApp) || null;

  function loadSdk() {
    if (window.SKINS_DEMO) return Promise.resolve();
    return new Promise((resolve) => {
      const s = document.createElement("script");
      s.src = "https://telegram.org/js/telegram-web-app.js";
      s.onload = resolve;
      s.onerror = resolve; // вне Telegram работаем в режиме разработки
      document.head.appendChild(s);
    });
  }

  function devUser() {
    try { return localStorage.getItem("devUser") || "1"; } catch (e) { return "1"; }
  }

  async function call(method, path, body) {
    if (window.SKINS_DEMO) return window.MockApi.handle(method, path, body);
    const headers = { "Content-Type": "application/json" };
    const t = tg();
    if (t && t.initData) headers["X-Telegram-Init-Data"] = t.initData;
    else headers["X-Dev-User"] = devUser();
    let resp;
    try {
      resp = await fetch(path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw Object.assign(new Error("Нет связи. Попробуй ещё раз."), { code: "network" });
    }
    const data = await resp.json().catch(() => ({ error: "Сервер не ответил. Попробуй ещё раз." }));
    if (!resp.ok) throw Object.assign(new Error(data.error || "Ошибка"), { code: data.code, data });
    return data;
  }

  window.Api = {
    ready: loadSdk,
    tg,
    get: (path) => call("GET", path),
    post: (path, body) => call("POST", path, body || {}),
  };
})();
