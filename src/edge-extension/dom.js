/* Stable message identity comes from Teams data-mid, never message text. */
(function (scope) {
  const selectors = {
    title: '[data-tid="chat-title"]',
    messages: '[data-tid="chat-pane-message"][data-mid]',
    text: '[data-message-content]',
    composer: '[data-tid="ckeditor"][contenteditable="true"]',
    send: '[data-tid="sendMessageCommands-send"]',
    menu: '[data-tid="message-actions-menu-hidden-button"]'
  };
  function normalize(text) { return text.replace(/\r\n/g, "\n").trim(); }
  function title(document) {
    const heading = document.querySelector(selectors.title);
    // A group can contain the same person: require exactly one participant.
    return heading && heading.querySelectorAll('[role="listitem"]').length === 1 ? normalize(heading.innerText) : "";
  }
  function messages(document) {
    return [...document.querySelectorAll(selectors.messages)].map(element => {
      const id = element.getAttribute("data-mid");
      const wrapper = element.closest('[data-testid="message-wrapper"]') || element;
      const text = element.querySelector(selectors.text)?.innerText || "";
      const status = document.getElementById("read-status-icon-" + id)?.getAttribute("aria-label") || "";
      return { id, text: normalize(text), date: Math.floor(Date.parse(wrapper.querySelector("time")?.dateTime || "") / 1000),
        edited: !!document.getElementById("edited-" + id),
        sent: /^(已发送|已送达|已读|Sent|Delivered|Read|送信済み|既読)/i.test(status),
        deleted: /^(此消息已删除|此邮件已删除|This message has been deleted|このメッセージは削除されました)/i.test(normalize(element.innerText)) };
    }).filter(item => /^[0-9]{10,20}$/.test(item.id));
  }
  scope.WorkLinkDOM = { selectors, normalize, title, messages };
  if (typeof module !== "undefined") module.exports = scope.WorkLinkDOM;
})(typeof globalThis !== "undefined" ? globalThis : window);
