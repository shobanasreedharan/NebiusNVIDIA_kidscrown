import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import App from "../App.jsx";
import { AuthProvider, TOKEN_KEY } from "../auth.jsx";
import { FamilyProvider } from "../family.jsx";

function json(status, body) {
  return new Response(status === 204 ? null : JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

const TEMPLATES = {
  7: { band: "6â€“8", min_age: 6, max_age: 8, policy_text: "TEMPLATE for ages 6-8" },
  8: { band: "6â€“8", min_age: 6, max_age: 8, policy_text: "TEMPLATE for ages 6-8" },
  10: { band: "9â€“12", min_age: 9, max_age: 12, policy_text: "TEMPLATE for ages 9-12" },
};

// A small stateful stand-in for the account, family, and children routes.
function fakeBackend({ profiles = [], family = { id: 1, name: "Ram", policy_text: "No gaming videos." } } = {}) {
  const state = { profiles: [...profiles], family: { ...family }, name: "Shobana", nextId: 50, calls: [] };
  const me = () => ({ id: 1, name: state.name, email: "shobana@example.com", family: state.family });
  const handler = vi.fn(async (input, options = {}) => {
    const url = new URL(String(input));
    const method = options.method || "GET";
    const body = options.body ? JSON.parse(options.body) : null;
    const path = url.pathname;
    state.calls.push({ method, path, body });

    if (method === "GET" && path === "/me") return json(200, me());
    if (method === "PUT" && path === "/me") {
      state.name = body.name;
      return json(200, me());
    }
    if (method === "PUT" && path === "/family") {
      state.family = { ...state.family, ...body };
      return json(200, state.family);
    }
    if (method === "GET" && path === "/profiles") return json(200, state.profiles);
    if (method === "POST" && path === "/profiles") {
      if (state.profiles.length >= 5) return json(400, { detail: "A family can have at most 5 children" });
      const source = body.policy_text === TEMPLATES[body.age]?.policy_text ? "template" : "custom";
      const created = { id: state.nextId++, family_id: 1, created_at: "2026-09-20T00:00:00", policy_source: source, ...body };
      state.profiles.push(created);
      return json(201, created);
    }
    const one = path.match(/^\/profiles\/(\d+)$/);
    if (one && method === "PUT") {
      const found = state.profiles.find((p) => p.id === Number(one[1]));
      Object.assign(found, body);
      return json(200, found);
    }
    if (one && method === "DELETE") {
      state.profiles = state.profiles.filter((p) => p.id !== Number(one[1]));
      return json(204);
    }
    const template = path.match(/^\/policy-templates\/(\d+)$/);
    if (template && method === "GET") return json(200, TEMPLATES[Number(template[1])]);
    if (method === "GET" && /\/(queue|channel-rules)$/.test(path)) return json(200, []);
    return json(404, { detail: `unhandled ${method} ${path}` });
  });
  return { handler, state };
}

const KID = (id, name, age, source = "template") => ({ id, family_id: 1, name, age, policy_text: `TEMPLATE for ages ${age}`, policy_source: source, created_at: "2026-09-19T00:00:00" });

let backend;
async function open(path, options) {
  backend = fakeBackend(options);
  vi.stubGlobal("fetch", backend.handler);
  localStorage.setItem(TOKEN_KEY, "tok");
  const user = userEvent.setup();
  render(
    <MemoryRouter initialEntries={[path]}>
      <AuthProvider>
        <FamilyProvider>
          <App />
        </FamilyProvider>
      </AuthProvider>
    </MemoryRouter>,
  );
  return user;
}
const calls = (method, path) => backend.state.calls.filter((c) => c.method === method && c.path === path);

beforeEach(() => vi.unstubAllGlobals());

describe("onboarding", () => {
  it("sends a family with no children to onboarding, then to the main view when finished", async () => {
    const user = await open("/");
    expect(await screen.findByRole("heading", { name: "Welcome, Shobana" })).toBeInTheDocument();
    expect(screen.getByText("Step 1 of 2")).toBeInTheDocument();
    expect(screen.getByLabelText("Family name")).toHaveValue("Ram");
    expect(screen.getByLabelText("Family rules")).toHaveValue("No gaming videos.");
    const familyRules = screen.getByLabelText("Family rules");
    expect(familyRules).toHaveAttribute("maxLength", "2000");
    expect(screen.getByText(`${"No gaming videos.".length} / 2,000`)).toBeInTheDocument();

    // Nothing changed, so nothing is saved.
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Add your children" })).toBeInTheDocument();
    expect(calls("PUT", "/family")).toHaveLength(0);
    expect(screen.getByRole("button", { name: "Finish" })).toBeDisabled();

    // Choosing an age fills in the suggested rules.
    await user.type(screen.getByLabelText("Name or nickname"), "Ava");
    await user.selectOptions(screen.getByLabelText("Age"), "7");
    expect(await screen.findByDisplayValue("TEMPLATE for ages 6-8")).toBeInTheDocument();
    expect(screen.getByText(/Suggested for ages 6â€“8/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Add child" }));

    expect(await screen.findByText("Ava, age 7")).toBeInTheDocument();
    expect(calls("POST", "/profiles")[0].body).toEqual({ name: "Ava", age: 7, policy_text: "TEMPLATE for ages 6-8" });

    await user.click(screen.getByRole("button", { name: "Finish" }));
    expect(await screen.findByRole("heading", { name: "Ram" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /Ava/ })).toBeInTheDocument();
  });

  it("saves changes to the family rules before moving on", async () => {
    const user = await open("/onboarding");
    const rules = await screen.findByLabelText("Family rules");
    await user.clear(rules);
    await user.type(rules, "No Minecraft.");
    expect(screen.getByText(`${"No Minecraft.".length} / 2,000`)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Continue" }));
    expect(await screen.findByRole("heading", { name: "Add your children" })).toBeInTheDocument();
    expect(calls("PUT", "/family")[0].body).toEqual({ name: "Ram", policy_text: "No Minecraft." });
  });

  it("can add a second child and stops offering the form at five", async () => {
    const four = [KID(1, "A", 7), KID(2, "B", 7), KID(3, "C", 7), KID(4, "D", 7)];
    const user = await open("/onboarding", { profiles: four });
    await user.click(await screen.findByRole("button", { name: "Continue" }));
    await user.type(await screen.findByLabelText("Name or nickname"), "E");
    await user.selectOptions(screen.getByLabelText("Age"), "7");
    await screen.findByDisplayValue("TEMPLATE for ages 6-8");
    await user.click(screen.getByRole("button", { name: "Add child" }));

    expect(await screen.findByText(/You've added the maximum of 5 children/)).toBeInTheDocument();
    expect(screen.queryByLabelText("Name or nickname")).not.toBeInTheDocument();
  });
});

describe("the add-child form", () => {
  async function openForm() {
    const user = await open("/children/new", { profiles: [KID(1, "Ava", 7)] });
    await screen.findByLabelText("Name or nickname");
    return user;
  }

  it("switches to a new age's suggestion while the rules are untouched", async () => {
    const user = await openForm();
    await user.selectOptions(screen.getByLabelText("Age"), "7");
    const rules = await screen.findByDisplayValue("TEMPLATE for ages 6-8");
    expect(rules).toHaveAttribute("maxLength", "2000");
    expect(screen.getByText(`${"TEMPLATE for ages 6-8".length} / 2,000`)).toBeInTheDocument();
    await user.selectOptions(screen.getByLabelText("Age"), "10");
    expect(await screen.findByDisplayValue("TEMPLATE for ages 9-12")).toBeInTheDocument();
  });

  it("keeps the parent's edits when the age changes, and can reload the suggestion", async () => {
    const user = await openForm();
    await user.selectOptions(screen.getByLabelText("Age"), "7");
    const rules = await screen.findByLabelText(/Rules for/);
    await user.clear(rules);
    await user.type(rules, "My own rules.");

    await user.selectOptions(screen.getByLabelText("Age"), "10");
    expect(await screen.findByText(/Your edits are kept/)).toBeInTheDocument();
    expect(screen.getByLabelText(/Rules for/)).toHaveValue("My own rules.");

    await user.click(screen.getByRole("button", { name: "Use the age template" }));
    expect(screen.getByLabelText(/Rules for/)).toHaveValue("TEMPLATE for ages 9-12");
  });

  it("sends the edited rules and lands on the new child's page", async () => {
    const user = await openForm();
    await user.type(screen.getByLabelText("Name or nickname"), "Mira");
    await user.selectOptions(screen.getByLabelText("Age"), "10");
    const rules = await screen.findByLabelText(/Rules for Mira/);
    await user.clear(rules);
    await user.type(rules, "Only educational videos.");
    await user.click(screen.getByRole("button", { name: "Add child" }));

    expect(calls("POST", "/profiles")[0].body).toEqual({ name: "Mira", age: 10, policy_text: "Only educational videos." });
    expect(await screen.findByRole("heading", { name: "Mira" })).toBeInTheDocument();
    expect(screen.getByLabelText("Viewing")).toHaveValue("50");
  });

  it("explains that five is the limit", async () => {
    const five = [1, 2, 3, 4, 5].map((n) => KID(n, `Kid${n}`, 7));
    await open("/children/new", { profiles: five });
    expect(await screen.findByText(/You already have 5 children, the maximum/)).toBeInTheDocument();
  });
});

describe("the profile page", () => {
  it("saves a new name and new family rules", async () => {
    const user = await open("/profile", { profiles: [KID(1, "Ava", 7)] });
    const name = await screen.findByLabelText("Your name");
    await user.clear(name);
    await user.type(name, "Shobana R");
    await user.click(screen.getByRole("button", { name: "Save name" }));
    expect(await screen.findByText("Saved.")).toBeInTheDocument();
    expect(calls("PUT", "/me")[0].body).toEqual({ name: "Shobana R" });
    expect(screen.getByRole("button", { name: /Shobana R/ })).toBeInTheDocument(); // the top bar follows

    const rules = screen.getByLabelText("Family rules");
    await user.clear(rules);
    await user.type(rules, "No MrBeast.");
    await user.click(screen.getByRole("button", { name: "Save family" }));
    expect(await screen.findByText(/Future checks use the new family rules for every child/)).toBeInTheDocument();
    expect(calls("PUT", "/family")[0].body).toEqual({ name: "Ram", policy_text: "No MrBeast." });
  });

  it("edits a child's name and age", async () => {
    const user = await open("/profile", { profiles: [KID(1, "Ava", 7)] });
    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const name = screen.getByLabelText("Name or nickname");
    await user.clear(name);
    await user.type(name, "Ava K");
    await user.selectOptions(screen.getByLabelText("Age"), "8");
    expect(screen.getByText(/rules switch to the new age's template/)).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Save" }));

    await screen.findByText("Ava K", { selector: ".rule-name" });
    expect(calls("PUT", "/profiles/1")[0].body).toEqual({ name: "Ava K", age: 8 });
  });

  it("asks before removing a child, and only removes on confirmation", async () => {
    const user = await open("/profile", { profiles: [KID(1, "Ava", 7), KID(2, "Mira", 10)] });
    const rows = await screen.findAllByRole("button", { name: "Remove" });
    await user.click(rows[0]);
    const dialog = screen.getByRole("alertdialog", { name: "Remove Ava" });
    expect(within(dialog).getByText(/can't be undone/)).toBeInTheDocument();

    await user.click(within(dialog).getByRole("button", { name: "Keep" }));
    expect(calls("DELETE", "/profiles/1")).toHaveLength(0);
    expect(screen.getByText("Ava", { selector: ".rule-name" })).toBeInTheDocument();

    await user.click(screen.getAllByRole("button", { name: "Remove" })[0]);
    await user.click(within(screen.getByRole("alertdialog")).getByRole("button", { name: "Remove Ava" }));
    await waitFor(() => expect(screen.queryByText("Ava", { selector: ".rule-name" })).not.toBeInTheDocument());
    expect(calls("DELETE", "/profiles/1")).toHaveLength(1);
    expect(screen.getByText("Mira", { selector: ".rule-name" })).toBeInTheDocument();
    expect(within(screen.getByLabelText("Viewing")).queryByRole("option", { name: "Ava" })).toBeNull(); // the switcher follows
  });

  it("offers Add a child until there are five", async () => {
    await open("/profile", { profiles: [KID(1, "Ava", 7)] });
    expect(await screen.findByRole("link", { name: "Add a child" })).toHaveAttribute("href", "/children/new");
  });
});
