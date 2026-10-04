let threadId = null;
let busy = false;

const chatWindow = document.getElementById("chat-window");
const userInput = document.getElementById("user-input");
const sendBtn = document.getElementById("send-btn");
const newChatBtn = document.getElementById("new-chat-btn");

const SUGGESTIONS = [
  "Anime like Attack on Titan",
  "Suggest a rom-com",
  "Best anime for beginners",
  "Who is Levi Ackerman?",
];

function showWelcome() {
  const el = document.createElement("div");
  el.className = "welcome";
  el.innerHTML = `
    <h2>今日は何を観る？</h2>
    <p>What are we watching today? Ask about plots, characters and lore, or get a recommendation.</p>
    <div class="chips">${SUGGESTIONS.map(s => `<button class="chip" data-text="${s}">${s}</button>`).join("")}</div>
  `;
  chatWindow.appendChild(el);
}

async function startNewChat() {
  const res = await fetch("/new_chat", { method: "POST" });
  const data = await res.json();
  threadId = data.thread_id;
  chatWindow.innerHTML = "";
  showWelcome();
}

function formatBotText(text) {
  const escaped = text.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
  let formatted = escaped.replace(/^[ \t]*[*-][ \t]+/gm, "• ");        // markdown list markers -> bullets
  formatted = formatted.replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>");
  formatted = formatted.replace(/\*(.+?)\*/g, "<em>$1</em>");            // *Bleach* -> italic
  formatted = formatted.replace(/\|\|(.+?)\|\|/g, '<span class="spoiler" title="Spoiler: click to reveal" onclick="this.classList.add(\'revealed\')">$1</span>');
  formatted = formatted.replace(/\n/g, "<br>");
  return formatted;
}

function appendMessage(text, sender) {
  const welcome = chatWindow.querySelector(".welcome");
  if (welcome) welcome.remove();

  const msg = document.createElement("div");
  msg.className = `message ${sender}`;

  const bubble = document.createElement("div");
  bubble.className = "bubble";
  if (sender === "bot") {
    bubble.innerHTML = formatBotText(text);
    const avatar = document.createElement("div");
    avatar.className = "avatar";
    avatar.textContent = "オ";
    msg.appendChild(avatar);
  } else {
    bubble.textContent = text;
  }
  msg.appendChild(bubble);
  chatWindow.appendChild(msg);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return msg;
}

function showTyping() {
  const msg = document.createElement("div");
  msg.className = "message bot";
  msg.innerHTML = `<div class="avatar">オ</div>
    <div class="bubble"><div class="typing"><i></i><i></i><i></i><span>考え中…</span></div></div>`;
  chatWindow.appendChild(msg);
  chatWindow.scrollTop = chatWindow.scrollHeight;
  return msg;
}

function setBusy(state) {
  busy = state;
  sendBtn.disabled = state;
}

async function sendMessage(override) {
  const text = (override ?? userInput.value).trim();
  if (!text || busy) return;
  setBusy(true);

  try {
    if (!threadId) await startNewChat();
    appendMessage(text, "user");
    userInput.value = "";
    const typing = showTyping();

    try {
      const res = await fetch("/chat", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ thread_id: threadId, message: text })
      });
      const data = await res.json();
      typing.remove();
      appendMessage(data.reply || data.error || "Hmm, something went wrong. Try again?", "bot");
    } catch (err) {
      typing.remove();
      appendMessage("I couldn't reach the server. Please try again in a moment.", "bot");
    }
  } finally {
    setBusy(false);
    userInput.focus();
  }
}

// sakura petals (purely decorative)
function makePetals(count = 14) {
  const box = document.getElementById("petals");
  for (let i = 0; i < count; i++) {
    const p = document.createElement("span");
    p.className = "petal";
    const size = 8 + Math.random() * 9;
    p.style.width = `${size}px`;
    p.style.height = `${size}px`;
    p.style.left = `${Math.random() * 100}%`;
    p.style.animationDuration = `${14 + Math.random() * 16}s`;
    p.style.animationDelay = `${-Math.random() * 25}s`;
    box.appendChild(p);
  }
}

sendBtn.addEventListener("click", () => sendMessage());
userInput.addEventListener("keydown", (e) => {
  if (e.key === "Enter") sendMessage();
});
newChatBtn.addEventListener("click", startNewChat);
chatWindow.addEventListener("click", (e) => {
  const chip = e.target.closest(".chip");
  if (chip) sendMessage(chip.dataset.text);
});

makePetals();
startNewChat();