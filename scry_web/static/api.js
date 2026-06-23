// Thin fetch wrapper around the scry web API. Same-origin; no auth.
async function req(method, path, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const res = await fetch(path, opts);
  if (!res.ok) {
    let detail = res.statusText;
    try { detail = (await res.json()).detail || detail; } catch (_e) {}
    throw new Error(detail);
  }
  if (res.status === 204) return null;
  const ct = res.headers.get("content-type") || "";
  return ct.includes("application/json") ? res.json() : res.text();
}

export const api = {
  status: () => req("GET", "/api/status"),

  listLocations: () => req("GET", "/api/locations"),
  createWorkspace: (name) => req("POST", "/api/locations", { name }),
  openProject: (path) => req("POST", "/api/locations/open", { path }),
  locationConversations: (id) =>
    req("GET", `/api/locations/${id}/conversations`),

  createConversation: (location_id, title) =>
    req("POST", "/api/conversations", { location_id, title }),
  getConversation: (id) => req("GET", `/api/conversations/${id}`),
  exportConversation: (id) => req("GET", `/api/conversations/${id}/export`),
  upgradeConversation: (id, name) =>
    req("POST", `/api/conversations/${id}/upgrade`, { name }),

  postMessage: (cid, payload) =>
    req("POST", `/api/conversations/${cid}/messages`, payload),
  getRun: (id) => req("GET", `/api/runs/${id}`),
  answerRun: (id, payload) => req("POST", `/api/runs/${id}/answers`, payload),
  reveal: (path) => req("POST", "/api/reveal", { path }),

  async uploadAttachment(cid, file) {
    const fd = new FormData();
    fd.append("file", file);
    const res = await fetch(`/api/conversations/${cid}/attachments`, {
      method: "POST",
      body: fd,
    });
    if (!res.ok) throw new Error("upload failed");
    return res.json();
  },

  downloadUrl: (runId, index) =>
    `/api/runs/${runId}/download?index=${index}`,
};
