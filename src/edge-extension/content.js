/* Serialize effects; preserve drafts and stop on identity changes or uncertain results. */
(() => {
  const dom = WorkLinkDOM;
  // Teams can expand blank paragraphs on paste. Only reply comparisons accept
  // this rendering difference; command observations and personal drafts stay exact.
  const replyText = text => dom.normalize(text).replace(/\n{3,}/g, "\n\n");
  let busy = false, settings = null, observed = false;
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const transport = message => chrome.runtime.sendMessage(message);
  async function request(route, body = {}) {
    const reply = await transport({ type: "request", route, body });
    if (!reply?.ok) throw new Error("Bridge request failed");
    return reply.result;
  }
  function identity() {
    if (!settings || dom.title(document) !== settings.selfChatDisplayName) throw new Error("Wrong self chat");
    return { chatTitle: settings.selfChatDisplayName };
  }
  function observedMessages() {
    // Only command/reply text is transferred; ordinary personal messages stay on the page.
    return dom.messages(document).map(item => ({ ...item,
      text: /^(\/chat(?:\s|$)|\/choose(?:\s|$)|omp：)/.test(item.text) ? item.text : "",
      date: Number.isFinite(item.date) ? item.date : Math.floor(Date.now() / 1000) }));
  }
  async function waitFor(check, milliseconds = 12000) {
    const end = Date.now() + milliseconds;
    while (Date.now() < end) {
      identity();
      const found = check();
      if (found) return found;
      await sleep(250);
    }
    throw new Error("Page outcome could not be confirmed");
  }
  function buttonByText(pattern) {
    const menus = [...document.querySelectorAll('[role="menu"]')];
    if (menus.length !== 1) throw new Error("Ambiguous message menu");
    const buttons = [...menus[0].querySelectorAll('[role="menuitem"]')].filter(e => pattern.test(dom.normalize(e.getAttribute("aria-label") || e.innerText || "")));
    if (buttons.length !== 1) throw new Error("Ambiguous menu control");
    return buttons[0];
  }
  async function fill(editor, text) {
    const original = dom.normalize(editor.innerText);
    editor.focus();
    // Teams may update its CKEditor selection when focus enters the composer.
    await sleep(50);
    const selection = window.getSelection();
    const range = document.createRange();
    range.selectNodeContents(editor);
    selection.removeAllRanges();
    selection.addRange(range);
    // Let CKEditor observe the selection before pasting into its model.
    // Immediate paste can prepend text instead of replacing the old reply.
    await sleep(50);
    identity();
    // Use CKEditor's model-aware clipboard pipeline for both sends and edits.
    // DOM-only native insertion can appear correct but revert when saved.
    if (dom.normalize(editor.innerText) !== original) throw new Error("Editor text mismatch");
    const transfer = new DataTransfer();
    transfer.setData("text/plain", text);
    editor.dispatchEvent(new ClipboardEvent("paste", {bubbles: true, cancelable: true, clipboardData: transfer}));
    await waitFor(() => replyText(editor.innerText) === replyText(text), 1500);
  }

  async function reconcile(saved) {
    const rows = dom.messages(document);
    if (saved.job.kind === "send") {
      const matches = rows.filter(row => !saved.beforeIds.includes(row.id) && replyText(row.text) === replyText(saved.job.text) && row.sent);
      return matches.length === 1 ? matches[0].id : null;
    }
    const row = rows.find(item => item.id === saved.job.teams_id);
    if (saved.job.kind === "edit" && row && replyText(row.text) === replyText(saved.job.text)) return row.id;
    if (saved.job.kind === "delete" && row?.deleted) return row.id;
    return null;
  }
  async function complete(saved, state, teamsId, failureCode) {
    await request("complete", { ...identity(), id: saved.job.id, lease: saved.job.lease, state, teamsId, failureCode });
    if (state !== "unknown") await transport({ type: "pending-set", operation: null });
  }
  async function execute(job) {
    const saved = { job, beforeIds: dom.messages(document).map(row => row.id) };
    let effectStarted = false;
    // Persist the lease and baseline before touching the Teams editor.
    await transport({ type: "pending-set", operation: saved });
    try {
      identity();
      const composers = [...document.querySelectorAll(dom.selectors.composer)];
      const compose = composers[0];
      if (composers.length !== 1 || !compose || dom.normalize(compose.innerText)) throw new Error("Existing draft or edit preserved");
      if (job.kind === "send") {
        await fill(compose, job.text);
        // Pasting can replace the compact composer and detach its old button.
        // Resolve the current send control only after the editor has settled.
        const send = await waitFor(() => {
          const buttons = [...document.querySelectorAll(dom.selectors.send + ', [data-tid="newMessageCommands-send"]')]
            .filter(button => /^(发送|傳送|Send|送信)(?:\s|$)/i.test(button.getAttribute("aria-label") || ""));
          return buttons.length === 1 && !buttons[0].disabled && buttons[0].getAttribute("aria-disabled") !== "true" ? buttons[0] : null;
        }, 3000);
        identity();
        effectStarted = true;
        send.click();
      } else {
        const row = [...document.querySelectorAll(dom.selectors.messages)].find(e => e.getAttribute("data-mid") === job.teams_id);
        if (!row || replyText(row.querySelector(dom.selectors.text)?.innerText || "") !== replyText(job.expected_text)) {
          throw new Error("Reply not visible or was modified externally");
        }
        const menu = row.querySelector(dom.selectors.menu);
        if (!menu) throw new Error("Message menu unavailable");
        menu.click();
        let toolbar = null;
        try {
          await waitFor(() => {
            toolbar = document.getElementById(job.teams_id + "-popover-surface");
            return document.querySelector('[role="menu"]') || toolbar;
          }, 750);
        } catch { /* Try the keyboard context action below. */ }
        // Current Teams opens a message-bound toolbar before the overflow menu.
        const directEdit = toolbar?.querySelector('[data-tid="message-actions-edit"]');
        if (toolbar && !(job.kind === "edit" && directEdit)) {
          const more = toolbar.querySelector('[data-tid="message-actions-more"]');
          if (!more) throw new Error("Message menu unavailable");
          more.click();
          await waitFor(() => document.querySelector('[role="menu"]'), 3000);
        }
        // Some Teams builds expose the menu through the keyboard context action.
        if (!toolbar && !document.querySelector('[role="menu"]')) {
          row.focus();
          row.dispatchEvent(new KeyboardEvent("keydown", { key: "F10", code: "F10", shiftKey: true, bubbles: true }));
        }
        if (job.kind === "edit") {
          const edit = directEdit || await waitFor(() => {
            try { return buttonByText(/^(编辑|編輯|Edit|編集)$/i); } catch { return null; }
          }, 3000);
          edit.click();
          // Teams renders its inline edit composer outside the message body.
          const editor = await waitFor(() => {
            const editors = [...document.querySelectorAll(dom.selectors.composer)].filter(e => replyText(e.innerText) === replyText(job.expected_text));
            return editors.length === 1 ? editors[0] : null;
          }, 3000);
          await fill(editor, job.text);
          const save = await waitFor(() => {
            const candidates = [...document.querySelectorAll('[data-tid="newMessageCommands-send"]')];
            return candidates.length === 1 ? candidates[0] : null;
          }, 3000);
          identity();
          effectStarted = true;
          save.click();
        } else if (job.kind === "delete") {
          const remove = await waitFor(() => {
            try { return buttonByText(/^(删除(?:此消息)?|刪除|Delete(?: this message)?|削除)$/i); } catch { return null; }
          }, 3000);
          identity();
          effectStarted = true;
          remove.click();
        } else throw new Error("Unsupported effect");
      }
      const teamsId = await waitFor(() => {
        // This function only reads DOM; promise reconciliation is handled below.
        const rows = dom.messages(document);
        if (job.kind === "send") {
          const matches = rows.filter(r => !saved.beforeIds.includes(r.id) && replyText(r.text) === replyText(job.text) && r.sent);
          return matches.length === 1 ? matches[0].id : null;
        }
        const row = rows.find(r => r.id === job.teams_id);
        return job.kind === "edit" && row && replyText(row.text) === replyText(job.text) || job.kind === "delete" && row?.deleted ? job.teams_id : null;
      });
      await complete(saved, "succeeded", teamsId);
    } catch (error) {
      const codes = {
        "Existing draft or edit preserved": "draft_present",
        "Send control unavailable": "send_unavailable",
        "Editor rejected input": "editor_input_rejected",
        "Editor text mismatch": "editor_text_mismatch",
        "Page outcome could not be confirmed": "outcome_unconfirmed",
        "Reply not visible or was modified externally": "reply_changed",
        "Message menu unavailable": "menu_unavailable",
        "Wrong self chat": "identity_changed"
      };
      await complete(saved, effectStarted ? "unknown" : "failed", undefined, codes[error.message] || "adapter_error");
    }
  }
  async function tick() {
    if (busy || document.visibilityState !== "visible") return;
    busy = true;
    try {
      settings ||= await request("settings");
      identity();
      const saved = (await transport({ type: "pending-get" })).result;
      if (saved) {
        const found = await reconcile(saved);
        if (found) await complete(saved, "succeeded", found);
        // Never re-execute a lease whose browser outcome is unknown.
        else return;
      }
      await request("observe", { ...identity(), baseline: !observed, messages: observedMessages() });
      observed = true;
      const job = await request("next", identity());
      if (job) await execute(job);
    } catch {
      // No message bodies, account identifiers or keys are logged.
    } finally {
      busy = false;
    }
  }
  setInterval(tick, 1000);
  tick();
})();
