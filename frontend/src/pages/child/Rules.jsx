import { useState } from "react";
import { useAuth } from "../../auth.jsx";
import { useFamily } from "../../family.jsx";
import { friendlyMessage } from "../../api.js";
import Field from "../../components/Field.jsx";
import { ErrorPanel, Spinner } from "../../components/Feedback.jsx";
import PolicyText from "../../components/PolicyText.jsx";
import { MAX_CHILD_POLICY_CHARS } from "../../constants.js";

function RuleLabel({ action }) {
  const allow = action === "allow";
  return <span className={`badge ${allow ? "badge-allow" : "badge-block"}`}>{allow ? "Always allow" : "Always block"}</span>;
}

function PolicyEditor({ profile }) {
  const { api, user } = useAuth();
  const { replaceProfile } = useFamily();
  const [text, setText] = useState(profile.policy_text);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");

  const dirty = text.trim() !== profile.policy_text.trim();

  async function save(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const updated = await api.updateProfile(profile.id, { policy_text: text.trim() });
      replaceProfile(updated);
      setText(updated.policy_text);
      setMessage("Rules saved. Future checks use them.");
    } catch (err) {
      setError(friendlyMessage(err));
    } finally {
      setBusy(false);
    }
  }

  async function useTemplate() {
    setBusy(true);
    setError("");
    setMessage("");
    try {
      const template = await api.getTemplate(profile.age);
      setText(template.policy_text);
      setMessage(`The age ${template.band} template is loaded. Save to use it.`);
    } catch (err) {
      setError(friendlyMessage(err));
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" aria-labelledby={`policy-title-${profile.id}`}>
      <h2 id={`policy-title-${profile.id}`}>
        {profile.name}'s rules{" "}
        <span className="chip">{profile.policy_source === "template" ? "Age template" : "Custom rules"}</span>
      </h2>

      <details className="disclosure">
        <summary>Family rules that also apply</summary>
        <PolicyText text={user.family.policy_text} empty="No family rules yet." />
      </details>

      <form className="form" onSubmit={save} noValidate>
        <Field
          as="textarea"
          rows={12}
          label="Rules in plain language"
          hint="If these rules disagree with the family rules, these win. Future checks use the new rules; videos you've already checked keep their answer until you check them again."
          maxLength={MAX_CHILD_POLICY_CHARS}
          value={text}
          onChange={(event) => setText(event.target.value)}
        />
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        {message && (
          <p className="form-ok" role="status">
            {message}
          </p>
        )}
        <div className="button-row">
          <button type="submit" className="btn btn-primary" disabled={busy || !dirty || !text.trim()}>
            {busy ? "Saving…" : "Save rules"}
          </button>
          <button type="button" className="btn btn-secondary" disabled={busy} onClick={useTemplate}>
            Use the age template
          </button>
        </div>
      </form>
    </section>
  );
}

function ChannelRules({ profile, channelRules }) {
  const { rules, state, error: loadError, reload: load } = channelRules;
  const [busyId, setBusyId] = useState(null);
  const [actionError, setActionError] = useState("");
  const [form, setForm] = useState({ channel_id: "", channel_name: "", action: "block", reason: "" });
  const [adding, setAdding] = useState(false);

  async function remove(rule) {
    setBusyId(rule.id);
    setActionError("");
    try {
      await channelRules.remove(rule);
    } catch (err) {
      setActionError(friendlyMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  async function switchAction(rule) {
    setBusyId(rule.id);
    setActionError("");
    try {
      await channelRules.switchAction(rule);
    } catch (err) {
      setActionError(friendlyMessage(err));
    } finally {
      setBusyId(null);
    }
  }

  async function add(event) {
    event.preventDefault();
    setAdding(true);
    setActionError("");
    try {
      await channelRules.add({
        channel_id: form.channel_id.trim(),
        channel_name: form.channel_name.trim() || undefined,
        action: form.action,
        reason: form.reason.trim() || undefined,
      });
      setForm({ channel_id: "", channel_name: "", action: "block", reason: "" });
    } catch (err) {
      setActionError(friendlyMessage(err));
    } finally {
      setAdding(false);
    }
  }

  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));

  return (
    <section className="card" aria-labelledby={`channels-title-${profile.id}`}>
      <h2 id={`channels-title-${profile.id}`}>Channel rules</h2>
      <p className="hint">A channel with a rule is decided instantly, without asking the AI.</p>

      {state === "loading" && <Spinner label="Loading channel rules" />}
      {state === "error" && <ErrorPanel message={loadError} onRetry={load} />}

      {state === "ready" && rules.length === 0 && (
        <p>
          No channel rules yet. After you check a video, you can set its channel to always allow or always block.
        </p>
      )}

      {state === "ready" && rules.length > 0 && (
        <ul className="rule-list">
          {rules.map((rule) => (
            <li key={rule.id}>
              <div className="rule-main">
                <p className="rule-name">{rule.channel_name || rule.channel_id}</p>
                <RuleLabel action={rule.action} />
                {rule.reason && <p className="hint">{rule.reason}</p>}
              </div>
              <div className="button-row">
                <button type="button" className="btn btn-quiet" disabled={busyId === rule.id} onClick={() => switchAction(rule)}>
                  Switch to always {rule.action === "allow" ? "block" : "allow"}
                </button>
                <button type="button" className="btn btn-quiet" disabled={busyId === rule.id} onClick={() => remove(rule)}>
                  Remove
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {actionError && (
        <p className="form-error" role="alert">
          {actionError}
        </p>
      )}

      <details className="disclosure">
        <summary>Add a rule by channel ID</summary>
        <form className="form" onSubmit={add} noValidate>
          <p className="hint">The easiest way is to check a video first. Use this if you already have the channel ID (it starts with UC).</p>
          <Field label="Channel ID" autoComplete="off" value={form.channel_id} onChange={set("channel_id")} />
          <Field label="Channel name (optional)" autoComplete="off" value={form.channel_name} onChange={set("channel_name")} />
          <div className="field">
            <label htmlFor={`rule-action-${profile.id}`}>What to do</label>
            <select id={`rule-action-${profile.id}`} value={form.action} onChange={set("action")}>
              <option value="allow">Always allow</option>
              <option value="block">Always block</option>
            </select>
          </div>
          <Field label="Reason (optional)" autoComplete="off" value={form.reason} onChange={set("reason")} />
          <button type="submit" className="btn btn-primary" disabled={adding || !form.channel_id.trim()}>
            {adding ? "Adding…" : "Add rule"}
          </button>
        </form>
      </details>
    </section>
  );
}

export default function Rules({ profile, channelRules }) {
  return (
    <div>
      <PolicyEditor profile={profile} />
      <ChannelRules profile={profile} channelRules={channelRules} />
    </div>
  );
}
