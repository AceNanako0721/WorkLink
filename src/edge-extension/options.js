chrome.storage.local.get(["serviceUrl", "adapterToken"]).then(values => {
  if (values.serviceUrl) document.getElementById("serviceUrl").value = values.serviceUrl;
  if (values.adapterToken) document.getElementById("adapterToken").value = values.adapterToken;
});
document.getElementById("check").addEventListener("click", async () => {
  const status = document.getElementById("connection");
  try {
    const values = await chrome.storage.local.get(["serviceUrl", "adapterToken"]);
    const url = new URL(values.serviceUrl || "http://127.0.0.1:8765");
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.pathname !== "/" || url.username || url.password || url.search || url.hash) throw new Error();
    const response = await fetch(url.origin + "/adapter/status", {
      method: "POST", headers: { "Content-Type": "application/json", Authorization: "Bearer " + values.adapterToken },
      body: "{}", signal: AbortSignal.timeout(5000), credentials: "omit", redirect: "error"
    });
    const data = await response.json();
    if (!data.ok) throw new Error();
    status.textContent = (data.result.adapterOnline ? "自聊已连接。" : "服务可连接，自聊适配尚未上线。") + " 未完成操作：" + data.result.unfinished.length;
    if (data.result.adapterOnline && data.result.longPolling === false) {
      status.textContent += " 当前页面需要重新加载扩展并刷新自聊页，以启用持续连接。";
    }
  } catch { status.textContent = "连接失败，请检查服务地址、密钥和本地服务。"; }
});
document.getElementById("clear").addEventListener("click", async () => {
  if (!window.confirm("已在 Teams 核对结果并完成服务端处理？清除后不能通过浏览器自动补确认。")) return;
  await chrome.storage.local.remove("operation");
  document.getElementById("connection").textContent = "浏览器未确认记录已清除，请刷新 Teams 自聊页。";
});
document.getElementById("settings").addEventListener("submit", async event => {
  event.preventDefault();
  const status = document.getElementById("status");
  try {
    const url = new URL(document.getElementById("serviceUrl").value);
    const token = document.getElementById("adapterToken").value.trim();
    if (url.protocol !== "http:" || url.hostname !== "127.0.0.1" || url.pathname !== "/" || url.search || url.hash || url.username || url.password || token.length < 24) throw new Error();
    await chrome.storage.local.set({ serviceUrl: url.origin, adapterToken: token });
    status.textContent = "已保存，请在打开的 Teams 自聊页刷新一次。";
  } catch {
    status.textContent = "请填写有效的本机地址和适配密钥。";
  }
});
