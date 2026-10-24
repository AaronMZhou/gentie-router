// Thin fetch wrapper for the project/route API. `window` is only touched inside functions.

export class ApiError extends Error {
  constructor(status, detail) {
    super(`${status}: ${detail}`);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
  }
}

const base = () => (typeof window !== "undefined" && window.APP_CONFIG?.API_BASE) || "";

export async function apiSend(method, path, body) {
  let res;
  try {
    res = await fetch(base() + path, {
      method,
      headers: body === undefined ? {} : { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
  } catch (e) {
    throw new ApiError(0, e.message || "network error");
  }
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const j = await res.json();
      detail = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  return res.status === 204 ? null : res.json();
}

export const apiGet = (path) => apiSend("GET", path);

export const listProjects = () => apiGet("/projects");
export const getProject = (id) => apiGet(`/projects/${id}`);
export const createProject = (body) => apiSend("POST", "/projects", body);
export const updateProject = (id, body) => apiSend("PATCH", `/projects/${id}`, body);
export const deleteProject = (id) => apiSend("DELETE", `/projects/${id}`);

export const addRoute = (projectId, body) => apiSend("POST", `/projects/${projectId}/routes`, body);
export const getRoute = (id) => apiGet(`/routes/${id}`);
export const renameRoute = (id, name) => apiSend("PATCH", `/routes/${id}`, { name });
export const deleteRoute = (id) => apiSend("DELETE", `/routes/${id}`);

let assumptions;
export const getAssumptions = () => (assumptions ??= apiGet("/assumptions").catch((e) => { assumptions = undefined; throw e; }));
export const getStats = () => apiGet("/stats");
