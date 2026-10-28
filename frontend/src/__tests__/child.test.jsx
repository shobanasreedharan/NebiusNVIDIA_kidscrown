import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App.jsx";
import { AuthProvider, TOKEN_KEY, VIEW_KEY } from "../auth.jsx";
import { progressLabel } from "../pages/child/progress.js";
import { FamilyProvider } from "../family.jsx";

const FAMILY = { id: 1, name: "Ram", policy_text: "No gaming videos." };
const PARENT = { id: 1, name: "Shobana", email: "shobana@example.com", family: FAMILY };
const Ava = { id: 1, family_id: 1, name: "Ava", age: 7, policy_text: "Allow: science.\nBlock: scary.", policy_source: "template", created_at: "2026-09-19T00:00:00" };

const VIDEOS = {
  science0001: { id: 11, status: "allow", title: "How volcanoes work", channel_name: "Curious Minds", channel_id: "UCcurious", explanation: "Educational science.", confidence: 0.94, transcript_available: true },
  minecraft01: { id: 12, status: "block", title: "Minecraft survival, episode 12", channel_name: "BlockBuilders", channel_id: "UCblocks", explanation: "Gaming is blocked.", confidence: 0.97, transcript_available: true },
  unsurevid01: { id: 13, status: "ask_parent", title: "Opening 50 surprise eggs", channel_name: "ToyTown", channel_id: "UCtoytown", explanation: "Toy content to check first.", confidence: 0.62, transcript_available: false },
};

function json(status, body) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// A small stateful stand-in for the backend's child-scoped routes.
function fakeBackend({ queue = [], rules = [] } = {}) {
  const state = { queue: [...queue], rules: [...rules], nextRule: 100, profile: { ...Ava }, calls: [] };
  const handler = vi.fn(async (input, options = {}) => {
    const url = new URL(String(input));
    const method = options.method || "GET";
    const body = options.body ? JSON.parse(options.body) : null;
    state.calls.push({ method, path: url.pathname, body });
    const path = url.pathname;

    if (method === "GET" && path === "/me") return json(200, PARENT);
    if (method === "GET" && path === "/profiles") return json(200, [state.profile]);
    if (method === "POST" && path === "/classify" && body.youtube_url.includes("slowvid")) {
      // Never answers; ends only if the caller cancels.
      return new Promise((_, reject) => {
        options.signal?.addEventListener("abort", () => reject(Object.assign(new Error("Aborted"), { name: "AbortError" })));
      });
    }
    if (method === "POST" && path === "/classify") {
      const id = Object.keys(VIDEOS).find((key) => body.youtube_url.includes(key));
      const noId = "Could not find an 11-character YouTube video ID in the URL"; // the backend's real wording
      if (body.youtube_url.includes("badlink")) return json(400, { detail: noId });
      if (body.youtube_url.includes("outage")) return json(503, { detail: "YouTube quota exceeded" });
      if (body.youtube_url.includes("weird400")) return json(400, { detail: "Something else was wrong" });
      if (!id) return json(400, { detail: noId });
      const rule = state.rules.find((r) => r.channel_id === VIDEOS[id].channel_id);
      const verdict = rule ? { ...VIDEOS[id], status: rule.action, explanation: `You set this channel to always ${rule.action}.`, confidence: 1, decided_by_channel_rule: true } : { ...VIDEOS[id], decided_by_channel_rule: false };
      const row = { profile_id: body.profile_id, video_id: id, cached: false, created_at: "2026-09-20T00:00:00", ...verdict };
      if (row.status === "ask_parent" && !state.queue.find((q) => q.id === row.id)) state.queue.push(row);
      return json(200, row);
    }
    if (method === "GET" && path === "/profiles/1/queue") return json(200, state.queue);
    const resolve = path.match(/^\/queue\/(\d+)\/resolve$/);
    if (method === "POST" && resolve) {
      const id = Number(resolve[1]);
      if (!state.queue.find((q) => q.id === id)) return json(404, { detail: "Classification not found" });
      state.queue = state.queue.filter((q) => q.id !== id);
      return json(200, { id: 1, classification_id: id, decision: body.decision, comment: body.comment, created_at: "2026-09-20T00:00:00" });
    }
    if (method === "GET" && path === "/profiles/1/channel-rules/lookup") {
      return json(200, state.rules.find((r) => r.channel_id === url.searchParams.get("channel_id")) ?? null);
    }
    if (method === "GET" && path === "/profiles/1/channel-rules") return json(200, state.rules);
    if (method === "POST" && path === "/profiles/1/channel-rules") {
      if (state.rules.find((r) => r.channel_id === body.channel_id)) return json(400, { detail: "This channel already has a rule." });
      const rule = { id: state.nextRule++, profile_id: 1, channel_name: null, reason: null, created_at: "2026-09-20T00:00:00", ...body };
      state.rules.unshift(rule);
      return json(201, rule);
    }
    const del = path.match(/^\/profiles\/1\/channel-rules\/(\d+)$/);
    if (method === "DELETE" && del) {
      state.rules = state.rules.filter((r) => r.id !== Number(del[1]));
      return json(204);
    }
    if (method === "PUT" && path === "/profiles/1") {
      state.profile = { ...state.profile, ...body, policy_source: "custom" };
      return json(200, state.profile);
    }
    if (method === "GET" && path === "/policy-templates/7") return json(200, { band: "6â€“8", min_age: 6, max_age: 8, policy_text: "TEMPLATE TEXT for ages 6-8." });
    return json(404, { detail: `unhandled ${method} ${path}` });
  });
  return { handler, state };
}

let backend;
async function openAva({ queue, rules } = {}) {
  backend = fakeBackend({ queue, rules });
  vi.stubGlobal("fetch", backend.handler);
  localStorage.setItem(TOKEN_KEY, "tok");
  localStorage.setItem(VIEW_KEY, "1");
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={["/"]}>
      <AuthProvider>
        <FamilyProvider>
          <App />
        </FamilyProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
  await screen.findByRole("heading", { name: "Ava" });
  return user;
}

const callsTo = (method, path) => backend.state.calls.filter((c) => c.method === method && c.path === path);

async function checkVideo(user, link) {
  await user.type(screen.getByLabelText(/YouTube link/), link);
  await user.click(screen.getByRole("button", { name: "Check video" }));
}

beforeEach(() => vi.unstubAllGlobals());

describe("the child's tabs", () => {
  it("shows three tabs for the selected child, with the Ask parent count", async () => {
    await openAva({ queue: [{ ...VIDEOS.unsurevid01, video_id: "unsurevid01", profile_id: 1 }] });
    expect(screen.getByRole("tab", { name: "Check a video" })).toBeInTheDocument();
    expect(screen.getByRole("tab", { name: "Rules" })).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("tab", { name: /Ask parent/ })).toHaveTextContent("1"));
  });

  it("moves between tabs with the arrow keys", async () => {
    const user = await openAva();
    screen.getByRole("tab", { name: "Check a video" }).focus();
    await user.keyboard("{ArrowRight}");
    expect(screen.getByRole("tab", { name: /Ask parent/ })).toHaveAttribute("aria-selected", "true");
    await user.keyboard("{End}");
    expect(screen.getByRole("tab", { name: "Rules" })).toHaveAttribute("aria-selected", "true");
  });
});

describe("Check a video", () => {
  it("shows an allowed video with its title, channel, reason, and confidence", async () => {
    const user = await openAva();
    await checkVideo(user, "https://www.youtube.com/watch?v=science0001");
    const result = await screen.findByRole("region", { name: "Result" });
    expect(within(result).getByText("Allow")).toBeInTheDocument();
    expect(within(result).getByRole("heading", { name: "How volcanoes work" })).toBeInTheDocument();
    expect(within(result).getByText("Curious Minds")).toBeInTheDocument();
    expect(within(result).getByText("Educational science.")).toBeInTheDocument();
    expect(within(result).getByText("Confidence 94%")).toBeInTheDocument();
    expect(within(result).getByRole("link", { name: /Open on YouTube/ })).toHaveAttribute(
      "href",
      "https://www.youtube.com/watch?v=science0001",
    );
    expect(callsTo("POST", "/classify")[0].body).toEqual({ profile_id: 1, youtube_url: "https://www.youtube.com/watch?v=science0001" });
    expect(result.querySelector("img")).toHaveAttribute("src", "https://i.ytimg.com/vi/science0001/mqdefault.jpg");
  });

  it("pastes an example video from the demo strip", async () => {
    const user = await openAva();
    const strip = screen.getByRole("region", { name: "Try an example video" });
    await user.click(within(strip).getByRole("button", { name: /Solar system/ }));
    expect(screen.getByLabelText(/YouTube link/)).toHaveValue("https://www.youtube.com/watch?v=e8YzKyot4Pc");
    expect(screen.getByText(/Example link pasted/)).toBeInTheDocument();
    expect(callsTo("POST", "/classify")).toHaveLength(0);
  });

  it("hides the preview of a blocked video until asked", async () => {
    const user = await openAva();
    await checkVideo(user, "https://youtu.be/minecraft01");
    const result = await screen.findByRole("region", { name: "Result" });
    expect(within(result).getByText("Block")).toBeInTheDocument();
    expect(result.querySelector("img")).toBeNull();
    await user.click(within(result).getByRole("button", { name: "Show preview" }));
    expect(result.querySelector("img")).not.toBeNull();
  });

  it("puts an unsure video in the Ask parent list and says so", async () => {
    const user = await openAva();
    await checkVideo(user, "https://youtu.be/unsurevid01");
    const result = await screen.findByRole("region", { name: "Result" });
    expect(within(result).getByText("Ask parent")).toBeInTheDocument();
    expect(within(result).getByText("This video is in your Ask parent list.")).toBeInTheDocument();
    await waitFor(() => expect(screen.getByRole("tab", { name: /Ask parent/ })).toHaveTextContent("1"));

    await user.click(within(result).getByRole("button", { name: "Review it now" }));
    expect(screen.getByRole("tab", { name: /Ask parent/ })).toHaveAttribute("aria-selected", "true");
  });

  it("explains a bad link in plain words, and shows other server messages as they are", async () => {
    const user = await openAva();
    await checkVideo(user, "not-a-real-badlink");
    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That doesn't look like a YouTube video link. Copy it from the video's Share button.",
    );
    expect(screen.getByRole("alert")).not.toHaveTextContent("11-character");

    await user.clear(screen.getByLabelText(/YouTube link/));
    await checkVideo(user, "https://youtu.be/weird400");
    await waitFor(() => expect(screen.getByRole("alert")).toHaveTextContent("Something else was wrong"));

    await user.clear(screen.getByLabelText(/YouTube link/));
    await checkVideo(user, "https://youtu.be/outage00000");
    await waitFor(() =>
      expect(screen.getByRole("alert")).toHaveTextContent("couldn't reach YouTube or the AI service"),
    );
  });

  it("adds a channel rule from the result and can remove it", async () => {
    const user = await openAva();
    await checkVideo(user, "https://youtu.be/minecraft01");
    const result = await screen.findByRole("region", { name: "Result" });

    await user.click(await within(result).findByRole("button", { name: "Always block this channel" }));
    expect(await within(result).findByText(/will now always be blocked for Ava/)).toBeInTheDocument();
    expect(callsTo("POST", "/profiles/1/channel-rules")[0].body).toEqual({
      channel_id: "UCblocks",
      channel_name: "BlockBuilders",
      action: "block",
    });
    expect(within(result).getByText(/Rule for BlockBuilders/)).toBeInTheDocument();

    await user.click(within(result).getByRole("button", { name: "Remove rule" }));
    expect(await within(result).findByText(/Rule removed/)).toBeInTheDocument();
    expect(backend.state.rules).toHaveLength(0);
  });

  it("shows a rule added here on the Rules tab, and a rule removed there back here", async () => {
    const user = await openAva();
    await checkVideo(user, "https://youtu.be/minecraft01");
    const result = await screen.findByRole("region", { name: "Result" });
    await user.click(await within(result).findByRole("button", { name: "Always block this channel" }));
    await within(result).findByText(/Rule for BlockBuilders/);

    await user.click(screen.getByRole("tab", { name: "Rules" }));
    const row = (await screen.findByText("BlockBuilders", { selector: ".rule-name" })).closest("li");
    expect(within(row).getByText("Always block")).toBeInTheDocument();

    await user.click(within(row).getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.getByText(/No channel rules yet/)).toBeInTheDocument());

    await user.click(screen.getByRole("tab", { name: "Check a video" }));
    expect(within(screen.getByRole("region", { name: "Result" })).queryByText(/Rule for BlockBuilders/)).toBeNull();
    expect(within(screen.getByRole("region", { name: "Result" })).getByRole("button", { name: "Always block this channel" })).toBeInTheDocument();
  });

  it("shows a channel-rule decision as the reason it was decided", async () => {
    const rule = { id: 5, profile_id: 1, channel_id: "UCcurious", channel_name: "Curious Minds", action: "block", reason: null };
    const user = await openAva({ rules: [rule] });
    await checkVideo(user, "https://youtu.be/science0001");
    const result = await screen.findByRole("region", { name: "Result" });
    expect(within(result).getByText("Block")).toBeInTheDocument();
    expect(within(result).getByText("Your channel rule")).toBeInTheDocument();
    expect(await within(result).findByText(/Rule for Curious Minds/)).toBeInTheDocument();
  });
});

describe("a slow check", () => {
  it("words the wait honestly as it goes on", () => {
    expect(progressLabel(0)).toBe("Checking the video");
    expect(progressLabel(7)).toBe("Checking the video");
    expect(progressLabel(8)).toMatch(/Still working.*transcript/);
    expect(progressLabel(25)).toMatch(/up to 30 seconds/);
  });

  it("can be cancelled, which is not treated as an error", async () => {
    const user = await openAva();
    await checkVideo(user, "https://youtu.be/slowvid0001");
    expect(await screen.findByText(/Checking the video/)).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Cancel" }));
    expect(await screen.findByText(/Cancelled\. The check may still finish in the background/)).toBeInTheDocument();
    expect(screen.queryByRole("alert")).not.toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Check video" })).toBeEnabled();

    // and the form works again
    await user.clear(screen.getByLabelText(/YouTube link/));
    await checkVideo(user, "https://www.youtube.com/watch?v=science0001");
    expect(await screen.findByRole("region", { name: "Result" })).toBeInTheDocument();
  });

  it("keeps the wait message going while the server takes its time", async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    try {
      const user = await openAva();
      await checkVideo(user, "https://youtu.be/slowvid0002");
      await screen.findByText(/Checking the video/);
      await act(async () => {
        vi.advanceTimersByTime(9000);
      });
      expect(await screen.findByText(/Still working\. Reading a video's transcript/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });
});

describe("Ask parent", () => {
  const item = { id: 13, profile_id: 1, video_id: "unsurevid01", ...VIDEOS.unsurevid01 };

  it("approves an item with a note and removes it from the list", async () => {
    const user = await openAva({ queue: [item] });
    await user.click(screen.getByRole("tab", { name: /Ask parent/ }));
    const panel = screen.getByRole("tabpanel", { name: /Ask parent/ });
    expect(within(panel).getByText("Opening 50 surprise eggs")).toBeInTheDocument();
    expect(within(panel).getByText("ToyTown")).toBeInTheDocument();

    await user.click(within(panel).getByRole("button", { name: "Add a note" }));
    await user.type(within(panel).getByLabelText("Note (optional)"), "OK on weekends");
    await user.click(within(panel).getByRole("button", { name: "Approve" }));

    await waitFor(() => expect(within(panel).getByText("Nothing to review")).toBeInTheDocument());
    expect(callsTo("POST", "/queue/13/resolve")[0].body).toEqual({ decision: "approve", comment: "OK on weekends" });
    expect(screen.getByRole("tab", { name: /Ask parent/ })).not.toHaveTextContent("1");
  });

  it("blocks an item without a note", async () => {
    const user = await openAva({ queue: [item] });
    await user.click(screen.getByRole("tab", { name: /Ask parent/ }));
    await user.click(screen.getByRole("button", { name: "Block" }));
    await waitFor(() => expect(callsTo("POST", "/queue/13/resolve")).toHaveLength(1));
    expect(callsTo("POST", "/queue/13/resolve")[0].body).toEqual({ decision: "block", comment: null });
  });

  it("shows an empty state when there is nothing to review", async () => {
    const user = await openAva();
    await user.click(screen.getByRole("tab", { name: /Ask parent/ }));
    expect(await screen.findByText("Nothing to review")).toBeInTheDocument();
  });

  it("copes with a video whose details can't be looked up", async () => {
    const user = await openAva({ queue: [{ ...item, title: null, channel_name: null }] });
    await user.click(screen.getByRole("tab", { name: /Ask parent/ }));
    expect(await screen.findByRole("heading", { name: "Video details unavailable" })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /Open on YouTube/ })).toBeInTheDocument();
  });
});

describe("Rules", () => {
  it("saves edited rules and marks them as custom", async () => {
    const user = await openAva();
    await user.click(screen.getByRole("tab", { name: "Rules" }));
    const box = await screen.findByLabelText("Rules in plain language");
    expect(screen.getByRole("button", { name: "Save rules" })).toBeDisabled(); // nothing changed yet

    await user.clear(box);
    await user.type(box, "Only science and animals.");
    expect(screen.getByText(`${"Only science and animals.".length} / 2,000`)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save rules" }));

    expect(await screen.findByText("Rules saved. Future checks use them.")).toBeInTheDocument();
    expect(callsTo("PUT", "/profiles/1")[0].body).toEqual({ policy_text: "Only science and animals." });
    expect(screen.getAllByText("Custom rules").length).toBeGreaterThan(0);
  });

  it("loads the age template into the box without saving it", async () => {
    const user = await openAva();
    await user.click(screen.getByRole("tab", { name: "Rules" }));
    await user.click(await screen.findByRole("button", { name: "Use the age template" }));
    expect(await screen.findByDisplayValue("TEMPLATE TEXT for ages 6-8.")).toBeInTheDocument();
    expect(screen.getByText(/template is loaded. Save to use it/)).toBeInTheDocument();
    expect(callsTo("PUT", "/profiles/1")).toHaveLength(0);
  });

  it("lists channel rules, switches one, and removes one", async () => {
    const rule = { id: 5, profile_id: 1, channel_id: "UCblocks", channel_name: "BlockBuilders", action: "block", reason: "gaming" };
    const user = await openAva({ rules: [rule] });
    await user.click(screen.getByRole("tab", { name: "Rules" }));
    const row = (await screen.findByText("BlockBuilders")).closest("li");
    expect(within(row).getByText("Always block")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "Switch to always allow" }));
    const switched = (await screen.findByText("BlockBuilders")).closest("li");
    await waitFor(() => expect(within(switched).getByText("Always allow")).toBeInTheDocument());
    const methods = backend.state.calls.filter((c) => c.path.startsWith("/profiles/1/channel-rules") && c.method !== "GET").map((c) => c.method);
    expect(methods).toEqual(["DELETE", "POST"]); // one rule per channel: delete, then create
    expect(callsTo("POST", "/profiles/1/channel-rules")[0].body).toMatchObject({ channel_id: "UCblocks", action: "allow", reason: "gaming" });

    await user.click(screen.getByRole("button", { name: "Remove" }));
    await waitFor(() => expect(screen.getByText(/No channel rules yet/)).toBeInTheDocument());
  });

  it("adds a rule by channel ID and shows the server's message for a duplicate", async () => {
    const user = await openAva();
    await user.click(screen.getByRole("tab", { name: "Rules" }));
    await user.click(await screen.findByText("Add a rule by channel ID"));
    await user.type(screen.getByLabelText("Channel ID"), "UCabc");
    await user.selectOptions(screen.getByLabelText("What to do"), "allow");
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    expect(await screen.findByText("UCabc")).toBeInTheDocument();

    await user.type(screen.getByLabelText("Channel ID"), "UCabc");
    await user.click(screen.getByRole("button", { name: "Add rule" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("This channel already has a rule.");
  });
});
