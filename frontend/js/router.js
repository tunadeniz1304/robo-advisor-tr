// Görünüm yönlendirici: nav düğmeleri + görünüm yükleyicileri.

import { $, $$, announce } from "./dom.js";

const loaders = {};
let current = "dashboard";

export function currentView() {
  return current;
}

export function registerView(name, loader) {
  loaders[name] = loader;
}

export async function show(name, { focus = false } = {}) {
  $$(".view").forEach((v) => v.classList.add("hidden"));
  const section = $(`#view-${name}`);
  if (!section) {
    return;
  }
  section.classList.remove("hidden");
  current = name;
  $$("#nav button").forEach((b) => {
    const active = b.dataset.view === name;
    b.classList.toggle("active", active);
    if (active) {
      b.setAttribute("aria-current", "page");
    } else {
      b.removeAttribute("aria-current");
    }
  });
  const heading = section.querySelector("h1, h2");
  if (focus && heading) {
    heading.setAttribute("tabindex", "-1");
    heading.focus();
  }
  try {
    await loaders[name]?.();
  } catch (err) {
    announce(`Görünüm yüklenemedi: ${err.message}`, "error");
  }
}

// Ok tuşları / Home / End ile nav düğmeleri arasında gezinme.
export function wireNavKeyboard(nav) {
  nav.addEventListener("keydown", (e) => {
    const buttons = $$("button:not(.hidden)", nav);
    const index = buttons.indexOf(document.activeElement);
    if (index < 0) {
      return;
    }
    const moves = {
      ArrowRight: index + 1,
      ArrowLeft: index - 1,
      Home: 0,
      End: buttons.length - 1,
    };
    if (!(e.key in moves)) {
      return;
    }
    e.preventDefault();
    const next = (moves[e.key] + buttons.length) % buttons.length;
    buttons[next].focus();
  });
}
