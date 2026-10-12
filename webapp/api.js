// Клиент API /v1 (docs/API.md). В Telegram подписывает запросы initData, в демо-режиме ходит в MockApi.
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

  function uuid() {
    if (crypto.randomUUID) return crypto.randomUUID();
    return Array.from(crypto.getRandomValues(new Uint8Array(16)), (b) => b.toString(16).padStart(2, "0")).join("");
  }

  async function call(method, path, body, key) {
    if (window.SKINS_DEMO) return window.MockApi.handle(method, path, body);
    const headers = { "Content-Type": "application/json" };
    const t = tg();
    if (t && t.initData) headers.Authorization = "tma " + t.initData;
    else headers["X-Dev-User"] = devUser();
    if (method === "POST" || method === "DELETE") headers["Idempotency-Key"] = key || uuid();
    let resp;
    try {
      resp = await fetch("/v1" + path, { method, headers, body: body ? JSON.stringify(body) : undefined });
    } catch (e) {
      throw Object.assign(new Error("Нет связи. Попробуй ещё раз."), { code: "NETWORK" });
    }
    const data = await resp.json().catch(() => null);
    if (!resp.ok) {
      const err = (data && data.error) || { code: "SERVER", message: "Сервер не ответил. Попробуй ещё раз." };
      throw Object.assign(new Error(err.message), err);
    }
    return data;
  }

  window.Api = {
    ready: loadSdk,
    tg,
    uuid,
    get: (path) => call("GET", path),
    post: (path, body, key) => call("POST", path, body || {}, key),
    put: (path, body) => call("PUT", path, body || {}),
    del: (path) => call("DELETE", path),
  };
})();
