import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App.jsx";
import { AuthProvider, TOKEN_KEY, VIEW_KEY } from "../auth.jsx";
import { FamilyProvider } from "../family.jsx";

const FAMILY = { id: 1, name: "Ram", policy_text: "No scary or violent content." };
const PARENT = { id: 1, name: "Shobana", email: "shobana@example.com", family: FAMILY };
const KIDS = [
  { id: 1, family_id: 1, name: "Ava", age: 7, policy_text: "Allow science. Block scary.", policy_source: "template", created_at: "2026-09-19T00:00:00" },
  { id: 2, family_id: 1, name: "Mira", age: 11, policy_text: "Only educational videos.", policy_source: "custom", created_at: "2026-09-19T00:00:01" },
];

function json(status, body) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

// A tiny in-memory stand-in for the backend.
function fakeBackend({ kids = KIDS, meStatus = 200, queues = {} } = {}) {
  const calls = [];
  const state = {
    queues: Object.fromEntries(kids.map((k) => [String(k.id), [...(queues[k.id] || [])]])),
  };
  const handler = vi.fn(async (input, options = {}) => {
    const url = new URL(String(input));
    const method = options.method || "GET";
    const key = `${method} ${url.pathname}`;
    const body = options.body ? JSON.parse(options.body) : null;
    calls.push({ key, options, body });
    switch (key) {
      case "POST /auth/login": {
        const loginBody = JSON.parse(options.body);
        if (loginBody.email === "shobana@example.com" && loginBody.password === "correct-horse-battery") {
          return json(200, { access_token: "tok-abc", token_type: "bearer", user: PARENT });
        }
        return json(401, { detail: "Invalid email or password" });
      }
      case "POST /auth/register":
        return json(201, { access_token: "tok-new", token_type: "bearer", user: PARENT });
      case "GET /me":
        return meStatus === 200 ? json(200, PARENT) : json(401, { detail: "Invalid or expired token" });
      case "GET /profiles":
        return json(200, kids);
      default: {
        const queueMatch = url.pathname.match(/^\/profiles\/(\d+)\/queue$/);
        if (method === "GET" && queueMatch) {
          return json(200, state.queues[queueMatch[1]] || []);
        }
        const resolve = url.pathname.match(/^\/queue\/(\d+)\/resolve$/);
        if (method === "POST" && resolve) {
          const id = Number(resolve[1]);
          for (const list of Object.values(state.queues)) {
            const idx = list.findIndex((item) => item.id === id);
            if (idx >= 0) list.splice(idx, 1);
          }
          return json(200, {
            id: 1,
            classification_id: id,
            decision: body.decision,
            comment: body.comment,
            created_at: "2026-09-20T00:00:00",
          });
        }
        return json(404, { detail: `unhandled ${key}` });
      }
    }
  });
  return { handler, calls, state };
}

function renderApp(path = "/") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <FamilyProvider>
          <App />
        </FamilyProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function signIn(user) {
  await user.type(screen.getByLabelText("Email"), "shobana@example.com");
  await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

let backend;
beforeEach(() => {
  backend = fakeBackend();
  vi.stubGlobal("fetch", backend.handler);
});
afterEach(() => vi.unstubAllGlobals());

describe("signing in", () => {
  it("sends a signed-out visitor to the sign-in form", async () => {
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(screen.getByText(/Every video gets one of three answers/)).toBeInTheDocument();
  });

  it("shows the server's message for a wrong password and stays signed out", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await user.type(screen.getByLabelText("Email"), "shobana@example.com");
    await user.type(screen.getByLabelText("Password"), "nope-nope-nope");
    await user.click(screen.getByRole("button", { name: "Sign in" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("Invalid email or password");
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("signs in, stores the token, and shows the family with its children", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user);

    expect(await screen.findByRole("heading", { name: "Ram" })).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBe("tok-abc");
    expect(screen.getByRole("button", { name: /Ava/ })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Mira/ })).toBeInTheDocument();

    const profilesCall = backend.calls.find((c) => c.key === "GET /profiles");
    expect(profilesCall.options.headers.Authorization).toBe("Bearer tok-abc");
  });

  it("says when the server can't be reached", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user);
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach KidsCrown");
  });
});

describe("registering", () => {
  it("creates the account with the family rules and lands in the app", async () => {
    const user = userEvent.setup();
    renderApp("/register");
    await user.type(screen.getByLabelText("Your name"), "Shobana");
    await user.type(screen.getByLabelText("Email"), "shobana@example.com");
    await user.type(screen.getByLabelText("Password"), "correct-horse-battery");
    await user.type(screen.getByLabelText("Family name"), "Ram");
    await user.type(screen.getByLabelText(/Family rules/), "No scary or violent content.");
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("heading", { name: "Ram" })).toBeInTheDocument();
    const call = backend.calls.find((c) => c.key === "POST /auth/register");
    expect(JSON.parse(call.options.body)).toEqual({
      parent_name: "Shobana",
      email: "shobana@example.com",
      password: "correct-horse-battery",
      family_name: "Ram",
      family_policy_text: "No scary or violent content.",
    });
  });

  it("keeps the button disabled until the required fields are filled", async () => {
    renderApp("/register");
    expect(await screen.findByRole("button", { name: "Create account" })).toBeDisabled();
  });
});

describe("the view switcher", () => {
  it("lists the parent as Main and each child, and scopes the page to the choice", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user);
    await screen.findByRole("heading", { name: "Ram" });

    const select = screen.getByLabelText("Viewing");
    const options = within(select).getAllByRole("option").map((o) => o.textContent);
    expect(options).toEqual(["Shobana (Main)", "Ava", "Mira"]);

    await user.selectOptions(select, "2");
    expect(await screen.findByRole("heading", { name: "Mira" })).toBeInTheDocument();
    expect(screen.getByText("Only educational videos.")).toBeInTheDocument();
    expect(screen.getAllByText("Custom rules").length).toBeGreaterThan(0); // header chip (and the Rules tab's)
    expect(localStorage.getItem(VIEW_KEY)).toBe("2");

    await user.selectOptions(select, "main");
    expect(await screen.findByRole("heading", { name: "Ram" })).toBeInTheDocument();
  });

  it("switches to a child when their card is clicked", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user);
    await user.click(await screen.findByRole("button", { name: /Ava/ }));
    expect(await screen.findByRole("heading", { name: "Ava" })).toBeInTheDocument();
    expect(screen.getByLabelText("Viewing")).toHaveValue("1");
  });
});

describe("saved sessions", () => {
  it("restores a saved session and the remembered child", async () => {
    localStorage.setItem(TOKEN_KEY, "tok-abc");
    localStorage.setItem(VIEW_KEY, "2");
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Mira" })).toBeInTheDocument();
    expect(screen.queryByRole("heading", { name: "Sign in" })).not.toBeInTheDocument();
  });

  it("falls back to Main when the remembered child no longer exists", async () => {
    localStorage.setItem(TOKEN_KEY, "tok-abc");
    localStorage.setItem(VIEW_KEY, "99");
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Ram" })).toBeInTheDocument();
    expect(screen.getByLabelText("Viewing")).toHaveValue("main");
  });

  it("returns to sign-in and clears the token when the saved one is rejected", async () => {
    vi.stubGlobal("fetch", fakeBackend({ meStatus: 401 }).handler);
    localStorage.setItem(TOKEN_KEY, "expired");
    renderApp("/");
    expect(await screen.findByRole("heading", { name: "Sign in" })).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("offers a retry when the server can't be reached while restoring", async () => {
    vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new TypeError("Failed to fetch")));
    localStorage.setItem(TOKEN_KEY, "tok-abc");
    renderApp("/");
    expect(await screen.findByRole("alert")).toHaveTextContent("Can't reach KidsCrown");
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
    expect(localStorage.getItem(TOKEN_KEY)).toBe("tok-abc"); // not signed out by an outage
  });
});

describe("the parent menu", () => {
  it("opens Profile and signs out", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user);
    await screen.findByRole("heading", { name: "Ram" });

    await user.click(screen.getByRole("button", { name: /Shobana/ }));
    await user.click(screen.getByRole("menuitem", { name: "Profile" }));
    expect(await screen.findByRole("heading", { name: "Profile" })).toBeInTheDocument();
    expect(screen.getByText("shobana@example.com")).toBeInTheDocument();
    expect(screen.getByText("No scary or violent content.")).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: /Shobana/ }));
    await user.click(screen.getByRole("menuitem", { name: "Sign out" }));
    await waitFor(() => expect(screen.getByRole("heading", { name: "Sign in" })).toBeInTheDocument());
    expect(localStorage.getItem(TOKEN_KEY)).toBeNull();
  });

  it("closes with Escape", async () => {
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user);
    await screen.findByRole("heading", { name: "Ram" });
    await user.click(screen.getByRole("button", { name: /Shobana/ }));
    expect(screen.getByRole("menu")).toBeInTheDocument();
    await user.keyboard("{Escape}");
    expect(screen.queryByRole("menu")).not.toBeInTheDocument();
  });
});

describe("the combined Ask parent queue", () => {
  const AvaItem = {
    id: 31,
    profile_id: 1,
    video_id: "unsurevid01",
    status: "ask_parent",
    title: "Opening 50 surprise eggs",
    channel_name: "ToyTown",
    explanation: "Needs a look.",
    confidence: 0.6,
    transcript_available: true,
    decided_by_channel_rule: false,
    created_at: "2026-09-21T00:00:00",
  };
  const miraItem = {
    id: 32,
    profile_id: 2,
    video_id: "gamevid0001",
    status: "ask_parent",
    title: "Minecraft build tips",
    channel_name: "BlockBuilders",
    explanation: "Gaming â€” check first.",
    confidence: 0.55,
    transcript_available: true,
    decided_by_channel_rule: false,
    created_at: "2026-09-22T00:00:00",
  };

  it("lists pending items from every child and can resolve one", async () => {
    backend = fakeBackend({ queues: { 1: [AvaItem], 2: [miraItem] } });
    vi.stubGlobal("fetch", backend.handler);
    const user = userEvent.setup();
    renderApp("/login");
    await signIn(user);

    const section = await screen.findByRole("region", { name: "Ask parent across children" });
    expect(within(section).getByText("Opening 50 surprise eggs")).toBeInTheDocument();
    expect(within(section).getByText("Minecraft build tips")).toBeInTheDocument();
    expect(within(section).getByText("For Ava")).toBeInTheDocument();
    expect(within(section).getByText("For Mira")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ava/ })).toHaveTextContent("1 to review");
    expect(screen.getByRole("button", { name: /Mira/ })).toHaveTextContent("1 to review");

    const miraCard = within(section).getByText("Minecraft build tips").closest("article");
    await user.click(within(miraCard).getByRole("button", { name: "Approve" }));
    await waitFor(() => expect(within(section).queryByText("Minecraft build tips")).not.toBeInTheDocument());
    expect(backend.calls.some((c) => c.key === "POST /queue/32/resolve")).toBe(true);
  });
});
