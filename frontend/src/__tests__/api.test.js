import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiError, createApi, friendlyMessage } from "../api.js";

function jsonResponse(status, body) {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

afterEach(() => vi.unstubAllGlobals());

describe("api client", () => {
  it("sends the bearer token and a JSON body on protected calls", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, { id: 1 }));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi({ baseUrl: "http://api.test", getToken: () => "tok123" });

    await api.classify(4, "https://youtu.be/abc");

    const [url, options] = fetchMock.mock.calls[0];
    expect(String(url)).toBe("http://api.test/classify");
    expect(options.method).toBe("POST");
    expect(options.headers.Authorization).toBe("Bearer tok123");
    expect(options.headers["Content-Type"]).toBe("application/json");
    expect(JSON.parse(options.body)).toEqual({ profile_id: 4, youtube_url: "https://youtu.be/abc" });
  });

  it("does not send a token on register or login", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, {}));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi({ baseUrl: "http://api.test", getToken: () => "tok123" });

    await api.login({ email: "a@b.co", password: "pw" });
    await api.register({ parent_name: "A" });

    for (const [, options] of fetchMock.mock.calls) {
      expect(options.headers.Authorization).toBeUndefined();
    }
  });

  it("puts query parameters on the rule lookup", async () => {
    const fetchMock = vi.fn().mockResolvedValue(jsonResponse(200, null));
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi({ baseUrl: "http://api.test", getToken: () => "t" });

    expect(await api.lookupRule(3, "UC123")).toBeNull();
    expect(String(fetchMock.mock.calls[0][0])).toBe("http://api.test/profiles/3/channel-rules/lookup?channel_id=UC123");
  });

  it("returns null for a 204 response", async () => {
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(new Response(null, { status: 204 })));
    const api = createApi({ baseUrl: "http://api.test", getToken: () => "t" });
    expect(await api.deleteProfile(9)).toBeNull();
  });

  it("ends the session on a 401 from a protected call, but not from login", async () => {
    const onUnauthorized = vi.fn();
    const api = createApi({ baseUrl: "http://api.test", getToken: () => "t", onUnauthorized });

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Invalid or expired token" })));
    await expect(api.me()).rejects.toMatchObject({ status: 401 });
    expect(onUnauthorized).toHaveBeenCalledTimes(1);

    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(401, { detail: "Invalid email or password" })));
    await expect(api.login({ email: "a@b.co", password: "x" })).rejects.toThrow("Invalid email or password");
    expect(onUnauthorized).toHaveBeenCalledTimes(1);
  });

  it("turns validation errors into plain sentences", async () => {
    const cases = [
      [{ type: "string_too_short", loc: ["body", "password"], ctx: { min_length: 8 } }, "Password must be at least 8 characters."],
      [{ type: "value_error", loc: ["body", "email"], msg: "Value error, Enter a valid email address" }, "Enter a valid email address"],
      [{ type: "missing", loc: ["body", "family_name"] }, "Family name is required."],
      [{ type: "greater_than_equal", loc: ["body", "age"] }, "Age must be between 3 and 17."],
    ];
    for (const [issue, expected] of cases) {
      vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, { detail: [issue] })));
      const api = createApi({ baseUrl: "http://api.test" });
      await expect(api.register({})).rejects.toThrow(expected);
    }
  });

  it("shows the server's combined-policy 422 message", async () => {
    const message = "The combined family and child policy must not exceed 4,500 characters.";
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(jsonResponse(422, { detail: message })));
    const api = createApi({ baseUrl: "http://api.test" });
    await expect(api.updateFamily({ policy_text: "rules" })).rejects.toThrow(message);
  });

  it("reports an unreachable server as status 0", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const api = createApi({ baseUrl: "http://api.test" });
    const error = await api.health().catch((e) => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(friendlyMessage(error)).toMatch(/Can't reach KidsCrown/);
  });

  it("passes a cancel signal through and does not treat a cancel as an outage", async () => {
    const controller = new AbortController();
    const fetchMock = vi.fn().mockImplementation(
      (_url, options) =>
        new Promise((_, reject) => {
          options.signal.addEventListener("abort", () => reject(Object.assign(new Error("Aborted"), { name: "AbortError" })));
        }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const api = createApi({ baseUrl: "http://api.test", getToken: () => "t" });

    const pending = api.classify(1, "https://youtu.be/abc", { signal: controller.signal });
    controller.abort();
    const error = await pending.catch((e) => e);
    expect(error.name).toBe("AbortError");
    expect(error).not.toBeInstanceOf(ApiError);
  });

  it("uses the plan's wording for a 503", () => {
    expect(friendlyMessage(new ApiError(503, "YouTube down"))).toMatch(/couldn't reach YouTube or the AI service/);
    expect(friendlyMessage(new ApiError(404, "Profile not found"))).toBe("Profile not found");
  });
});
