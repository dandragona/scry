// ui.js — in-app toasts + modal dialogs, replacing native alert/confirm/prompt.
// Dependency-free, themed to match the dark UI, and keyboard-accessible
// (Esc cancels, Enter confirms from the input, Tab is trapped inside the modal).

function toastHost() {
  let host = document.getElementById("toasts");
  if (!host) {
    host = document.createElement("div");
    host.id = "toasts";
    host.className = "toasts";
    host.setAttribute("aria-live", "polite");
    document.body.appendChild(host);
  }
  return host;
}

// type: "info" | "success" | "error". Returns a dismiss() fn.
export function toast(message, type = "info", timeout = 4500) {
  const host = toastHost();
  const t = document.createElement("div");
  t.className = `toast ${type}`;
  t.setAttribute("role", type === "error" ? "alert" : "status");

  const msg = document.createElement("span");
  msg.className = "toast-msg";
  msg.textContent = message;

  const close = document.createElement("button");
  close.className = "toast-close";
  close.setAttribute("aria-label", "Dismiss");
  close.textContent = "✕";

  let done = false;
  const dismiss = () => {
    if (done) return;
    done = true;
    t.classList.add("leaving");
    setTimeout(() => t.remove(), 180);
  };
  close.addEventListener("click", dismiss);

  t.append(msg, close);
  host.appendChild(t);
  if (timeout) setTimeout(dismiss, timeout);
  return dismiss;
}

// Internal modal builder. `field` truthy => a text input (prompt); else confirm.
function modal({ title, body, field, okText = "OK", cancelText = "Cancel", danger = false }) {
  return new Promise((resolve) => {
    const backdrop = document.createElement("div");
    backdrop.className = "modal-backdrop";

    const dialog = document.createElement("div");
    dialog.className = "modal";
    dialog.setAttribute("role", "dialog");
    dialog.setAttribute("aria-modal", "true");
    dialog.setAttribute("aria-labelledby", "modal-title");

    dialog.innerHTML = `
      <h3 id="modal-title" class="modal-title"></h3>
      ${body ? `<p class="modal-body"></p>` : ""}
      ${field ? `<input class="modal-input" type="text" />` : ""}
      <div class="modal-actions">
        <button class="btn ghost" data-act="cancel"></button>
        <button class="btn ${danger ? "danger" : "primary"}" data-act="ok"></button>
      </div>`;

    dialog.querySelector(".modal-title").textContent = title || "";
    if (body) dialog.querySelector(".modal-body").textContent = body;
    dialog.querySelector('[data-act="cancel"]').textContent = cancelText;
    dialog.querySelector('[data-act="ok"]').textContent = okText;

    const input = dialog.querySelector(".modal-input");
    if (input) {
      if (field.placeholder) input.placeholder = field.placeholder;
      if (field.value) input.value = field.value;
    }

    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);

    const prev = document.activeElement;
    const finish = (result) => {
      document.removeEventListener("keydown", onKey, true);
      backdrop.remove();
      if (prev && prev.focus) {
        try {
          prev.focus();
        } catch (_e) {
          /* element may be gone */
        }
      }
      resolve(result);
    };
    const onOk = () => finish(field ? input.value.trim() || null : true);
    const onCancel = () => finish(field ? null : false);

    dialog.querySelector('[data-act="ok"]').addEventListener("click", onOk);
    dialog.querySelector('[data-act="cancel"]').addEventListener("click", onCancel);
    backdrop.addEventListener("mousedown", (e) => {
      if (e.target === backdrop) onCancel();
    });

    const onKey = (e) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onCancel();
      } else if (e.key === "Enter" && input && document.activeElement === input) {
        e.preventDefault();
        onOk();
      } else if (e.key === "Tab") {
        const f = dialog.querySelectorAll("button, input");
        if (!f.length) return;
        const first = f[0];
        const last = f[f.length - 1];
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last.focus();
        } else if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first.focus();
        }
      }
    };
    document.addEventListener("keydown", onKey, true);
    (input || dialog.querySelector('[data-act="ok"]')).focus();
  });
}

// Resolves true (confirmed) / false (cancelled).
export function confirmDialog(message, opts = {}) {
  return modal({
    title: opts.title || "Please confirm",
    body: message,
    okText: opts.okText || "OK",
    cancelText: opts.cancelText || "Cancel",
    danger: opts.danger,
  });
}

// Resolves the trimmed string, or null if cancelled / left blank.
export function promptDialog(message, opts = {}) {
  return modal({
    title: opts.title || message,
    body: opts.title ? message : opts.body,
    field: { placeholder: opts.placeholder || "", value: opts.value || "" },
    okText: opts.okText || "OK",
    cancelText: opts.cancelText || "Cancel",
  });
}
