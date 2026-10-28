// Thin client for the KidsCrown backend. Every function returns parsed JSON
// (or null for 204) and throws ApiError on failure. It knows nothing about
// React, so it can be tested on its own.

export const API_URL = import.meta.env?.VITE_API_URL || "http://localhost:8000";

export class ApiError extends Error {
  constructor(status, message, detail) {
    super(message);
    this.name = "ApiError";
    this.status = status; // 0 = the server could not be reached
    this.detail = detail;
  }
}

const FIELD_LABELS = {
  parent_name: "Your name",
  email: "Email",
  password: "Password",
  family_name: "Family name",
  family_policy_text: "Family rules",
  name: "Name",
  age: "Age",
  policy_text: "Rules",
  youtube_url: "Video link",
  channel_id: "Channel",
};

function describeIssue(issue) {
  const field = Array.isArray(issue.loc) ? issue.loc[issue.loc.length - 1] : null;
  const label = FIELD_LABELS[field] || (typeof field === "string" ? field : "This field");
  const ctx = issue.ctx || {};
  switch (issue.type) {
    case "missing":
      return `${label} is required.`;
    case "string_too_short":
      return ctx.min_length === 1
        ? `${label} can't be empty.`
        : `${label} must be at least ${ctx.min_length} characters.`;
    case "string_too_long":
      return `${label} must be at most ${ctx.max_length} characters.`;
    case "greater_than_equal":
    case "less_than_equal":
      return field === "age" ? "Age must be between 3 and 17." : `${label} is out of range.`;
    case "int_parsing":
      return `${label} must be a number.`;
    case "value_error":
      return (issue.msg || "").replace(/^Value error,\s*/i, "") || `${label} isn't valid.`;
    default:
      return issue.msg ? `${label}: ${issue.msg}` : `${label} isn't valid.`;
  }
}

function messageFromDetail(detail, fallback) {
  if (typeof detail === "string" && detail) return detail;
  if (Array.isArray(detail) && detail.length) return describeIssue(detail[0]);
  return fallback;
}

// Turns any thrown error into a sentence that's safe to show a parent.
export function friendlyMessage(error) {
  if (!(error instanceof ApiError)) return "Something went wrong. Try again.";
  if (error.status === 0) return "Can't reach KidsCrown. Check your connection and try again.";
  if (error.status === 503) return "KidsCrown couldn't reach YouTube or the AI service. Try again in a moment.";
  if (error.status >= 500) return "Something went wrong on our side. Try again in a moment.";
  return error.message;
}

export function createApi({ baseUrl = API_URL, getToken = () => null, onUnauthorized = () => {} } = {}) {
  async function request(method, path, { body, params, auth = true, signal } = {}) {
    const url = new URL(path, baseUrl);
    if (params) {
      for (const [key, value] of Object.entries(params)) {
        if (value !== undefined && value !== null) url.searchParams.set(key, value);
      }
    }
    const headers = { Accept: "application/json" };
    const token = auth ? getToken() : null;
    if (token) headers.Authorization = `Bearer ${token}`;
    if (body !== undefined) headers["Content-Type"] = "application/json";

    let response;
    try {
      response = await fetch(url, {
        method,
        headers,
        body: body !== undefined ? JSON.stringify(body) : undefined,
        signal,
      });
    } catch (error) {
      if (error?.name === "AbortError") throw error; // the caller cancelled; that's not an outage
      throw new ApiError(0, "Can't reach KidsCrown. Check your connection and try again.");
    }

    if (response.status === 204) return null;

    let data = null;
    try {
      data = await response.json();
    } catch {
      data = null;
    }

    if (!response.ok) {
      // A rejected token on a protected call means the session is over.
      if (response.status === 401 && token) onUnauthorized();
      const fallback = `Request failed (${response.status}).`;
      throw new ApiError(response.status, messageFromDetail(data?.detail, fallback), data?.detail);
    }
    return data;
  }

  return {
    health: () => request("GET", "/health", { auth: false }),

    // Auth & account
    register: (payload) => request("POST", "/auth/register", { body: payload, auth: false }),
    login: (payload) => request("POST", "/auth/login", { body: payload, auth: false }),
    me: () => request("GET", "/me"),
    updateMe: (payload) => request("PUT", "/me", { body: payload }),

    // Family
    getFamily: () => request("GET", "/family"),
    updateFamily: (payload) => request("PUT", "/family", { body: payload }),

    // Age templates
    listTemplates: () => request("GET", "/policy-templates"),
    getTemplate: (age) => request("GET", `/policy-templates/${age}`),

    // Children
    listProfiles: () => request("GET", "/profiles"),
    createProfile: (payload) => request("POST", "/profiles", { body: payload }),
    getProfile: (id) => request("GET", `/profiles/${id}`),
    updateProfile: (id, payload) => request("PUT", `/profiles/${id}`, { body: payload }),
    deleteProfile: (id) => request("DELETE", `/profiles/${id}`),

    // Channel rules
    listRules: (profileId) => request("GET", `/profiles/${profileId}/channel-rules`),
    lookupRule: (profileId, channelId) =>
      request("GET", `/profiles/${profileId}/channel-rules/lookup`, { params: { channel_id: channelId } }),
    createRule: (profileId, payload) =>
      request("POST", `/profiles/${profileId}/channel-rules`, { body: payload }),
    deleteRule: (profileId, ruleId) =>
      request("DELETE", `/profiles/${profileId}/channel-rules/${ruleId}`),

    // Video search
    search: (query, { signal } = {}) => request("GET", "/search", { params: { q: query }, signal }),

    // Classification & Ask Parent
    classify: (profileId, youtubeUrl, { signal } = {}) =>
      request("POST", "/classify", { body: { profile_id: profileId, youtube_url: youtubeUrl }, signal }),
    queue: (profileId) => request("GET", `/profiles/${profileId}/queue`),
    resolve: (classificationId, payload) =>
      request("POST", `/queue/${classificationId}/resolve`, { body: payload }),
  };
}
