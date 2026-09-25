// Copilot: SSE akışlı sohbet (step / token / done olayları).

import { API, authHeaders } from "../api.js";
import { $, announce } from "../dom.js";
import { state } from "../state.js";

function message(cls, text) {
  const div = document.createElement("div");
  div.className = `msg ${cls}`;
  div.textContent = text;
  $("#chat").append(div);
  $("#chat").scrollTop = 1e9;
  return div;
}

function handleEvent(chunk, bot) {
  const event = /event: (\w+)/.exec(chunk)?.[1];
  const raw = chunk.split("data: ")[1];
  const data = raw ? JSON.parse(raw) : null;
  if (event === "step") {
    const step = document.createElement("div");
    step.className = "msg step";
    step.textContent = `Araç: ${data.tool}`;
    $("#chat").insertBefore(step, bot);
  } else if (event === "token") {
    bot.textContent += data;
  } else if (event === "done") {
    bot.title = `LLM: ${data.llm_mode}`;
  }
}

async function ask(question) {
  message("user", question);
  const bot = message("bot", "");
  bot.setAttribute("aria-busy", "true");
  const res = await fetch(`${API}/copilot/chat`, {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify({
      message: question,
      customer_id: state.customerId,
      portfolio_id: state.portfolioId,
    }),
  });
  if (!res.ok) {
    bot.textContent = "Copilot yanıt veremedi.";
    bot.removeAttribute("aria-busy");
    announce("Copilot yanıt veremedi.", "error");
    return;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) {
      break;
    }
    buffer += decoder.decode(value, { stream: true });
    let idx = buffer.indexOf("\n\n");
    while (idx >= 0) {
      handleEvent(buffer.slice(0, idx), bot);
      buffer = buffer.slice(idx + 2);
      idx = buffer.indexOf("\n\n");
    }
  }
  bot.removeAttribute("aria-busy");
  announce("Copilot yanıtı hazır.");
}

export function wireCopilot() {
  $("#copilot-chips").addEventListener("click", (e) => {
    const chip = e.target.closest("button.chip");
    if (chip) {
      ask(chip.textContent).catch((err) => announce(err.message, "error"));
    }
  });
  $("#chat-form").addEventListener("submit", (e) => {
    e.preventDefault();
    const input = $("#chat-input");
    const question = input.value.trim();
    if (question) {
      input.value = "";
      ask(question).catch((err) => announce(err.message, "error"));
    }
  });
}
