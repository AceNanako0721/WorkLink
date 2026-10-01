/* Only our Teams content script may use this authenticated loopback transport. */
chrome.storage.local.setAccessLevel({ accessLevel: "TRUSTED_CONTEXTS" });
chrome.action.onClicked.addListener(() => chrome.runtime.openOptionsPage());
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  if (sender.id !== chrome.runtime.id || !sender.tab ||
      !sender.url?.startsWith("https://teams.cloud.microsoft/")) return false;
  (async () => {
    const config = await chrome.storage.local.get(["serviceUrl", "adapterToken", "installationId"]);
    const url = new URL(config.serviceUrl || "http://127.0.0.1:8765");
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.pathname !== "/" || url.search || url.hash || url.username || url.password) {
      throw new Error("Use a loopback service URL.");
    }
    if (!config.adapterToken) throw new Error("Configure the adapter key first.");
    const installationId = config.installationId || crypto.randomUUID();
    if (!config.installationId) await chrome.storage.local.set({ installationId });
    const clientId = installationId + "_" + sender.tab.id;
    if (message.type === "pending-get") {
      return { ok: true, result: (await chrome.storage.local.get("operation" )).operation || null };
    }
    if (message.type === "pending-set") {
      await chrome.storage.local.set({ operation: message.operation });
      return { ok: true, result: true };
    }
    const routes = new Set(["settings", "observe", "next", "complete"]);
    if (message.type !== "request" || !routes.has(message.route)) throw new Error("Unsupported operation.");
    const response = await fetch(url.origin + "/adapter/" + message.route, {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + config.adapterToken },
      body: JSON.stringify({ ...message.body, clientId }), signal: AbortSignal.timeout(12000),
      credentials: "omit", redirect: "error"
    });
    return await response.json();
  })().then(respond).catch(() => respond({ ok: false, description: "Local service or adapter configuration unavailable." }));
  return true;
});
