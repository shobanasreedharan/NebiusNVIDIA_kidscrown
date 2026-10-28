import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import { useFamily } from "../family.jsx";
import { friendlyMessage } from "../api.js";
import Field from "../components/Field.jsx";
import { AGES, MAX_FAMILY_POLICY_CHARS, MAX_KIDS } from "../constants.js";

function useSaveState() {
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  async function run(action, successMessage) {
    setBusy(true);
    setMessage("");
    setError("");
    try {
      await action();
      setMessage(successMessage);
    } catch (err) {
      setError(friendlyMessage(err));
    } finally {
      setBusy(false);
    }
  }
  return { busy, message, error, run, clear: () => { setMessage(""); setError(""); } };
}

function Feedback({ message, error }) {
  return (
    <>
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
    </>
  );
}

function AccountSection() {
  const { api, user, updateUser } = useAuth();
  const [name, setName] = useState(user.name);
  const save = useSaveState();
  const dirty = name.trim() !== user.name;

  return (
    <section className="card" aria-labelledby="account-title">
      <h2 id="account-title">Your account</h2>
      <form
        className="form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          save.run(async () => updateUser(await api.updateMe({ name: name.trim() })), "Saved.");
        }}
      >
        <Field label="Your name" maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
        <div className="field">
          <span className="label-text">Email</span>
          <p className="static-value">{user.email}</p>
          <p className="hint">Your email can't be changed for now.</p>
        </div>
        <Feedback message={save.message} error={save.error} />
        <button type="submit" className="btn btn-primary" disabled={save.busy || !dirty || !name.trim()}>
          {save.busy ? "Saving…" : "Save name"}
        </button>
      </form>
    </section>
  );
}

function FamilySection() {
  const { api, user, updateUser } = useAuth();
  const [name, setName] = useState(user.family.name);
  const [rules, setRules] = useState(user.family.policy_text);
  const save = useSaveState();
  const dirty = name.trim() !== user.family.name || rules.trim() !== user.family.policy_text.trim();

  return (
    <section className="card" aria-labelledby="family-title">
      <h2 id="family-title">Your family</h2>
      <form
        className="form"
        noValidate
        onSubmit={(event) => {
          event.preventDefault();
          save.run(async () => {
            const family = await api.updateFamily({ name: name.trim(), policy_text: rules.trim() });
            updateUser({ ...user, family });
            setRules(family.policy_text);
          }, "Saved. Future checks use the new family rules for every child.");
        }}
      >
        <Field label="Family name" maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
        <Field
          as="textarea"
          rows={7}
          label="Family rules"
          hint="These apply to every child. A child's own rules win if they disagree."
          maxLength={MAX_FAMILY_POLICY_CHARS}
          value={rules}
          onChange={(event) => setRules(event.target.value)}
        />
        <Feedback message={save.message} error={save.error} />
        <button type="submit" className="btn btn-primary" disabled={save.busy || !dirty || !name.trim()}>
          {save.busy ? "Saving…" : "Save family"}
        </button>
      </form>
    </section>
  );
}

function ChildRow({ profile }) {
  const { api } = useAuth();
  const { replaceProfile, removeProfile } = useFamily();
  const [mode, setMode] = useState("view"); // view | edit | confirm
  const [name, setName] = useState(profile.name);
  const [age, setAge] = useState(String(profile.age));
  const save = useSaveState();

  const ageChanged = Number(age) !== profile.age;

  function startEdit() {
    setName(profile.name);
    setAge(String(profile.age));
    save.clear();
    setMode("edit");
  }

  if (mode === "edit") {
    return (
      <li>
        <form
          className="form"
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            save.run(async () => {
              replaceProfile(await api.updateProfile(profile.id, { name: name.trim(), age: Number(age) }));
              setMode("view");
            }, "");
          }}
        >
          <Field label="Name or nickname" maxLength={120} value={name} onChange={(event) => setName(event.target.value)} />
          <div className="field">
            <label htmlFor={`age-${profile.id}`}>Age</label>
            <select id={`age-${profile.id}`} value={age} onChange={(event) => setAge(event.target.value)}>
              {AGES.map((value) => (
                <option key={value} value={value}>
                  {value}
                </option>
              ))}
            </select>
            {ageChanged && (
              <p className="hint">
                If {profile.name} is still on an age template, their rules switch to the new age's template. Custom rules
                are never changed.
              </p>
            )}
          </div>
          <Feedback message="" error={save.error} />
          <div className="button-row">
            <button type="submit" className="btn btn-primary" disabled={save.busy || !name.trim()}>
              {save.busy ? "Saving…" : "Save"}
            </button>
            <button type="button" className="btn btn-secondary" onClick={() => setMode("view")}>
              Cancel
            </button>
          </div>
        </form>
      </li>
    );
  }

  if (mode === "confirm") {
    return (
      <li>
        <div className="confirm-box" role="alertdialog" aria-label={`Remove ${profile.name}`}>
          <p>
            Remove <strong>{profile.name}</strong>? Their rules, channel rules, and history are deleted. This can't be undone.
          </p>
          <Feedback message="" error={save.error} />
          <div className="button-row">
            <button
              type="button"
              className="btn btn-block"
              disabled={save.busy}
              onClick={() =>
                save.run(async () => {
                  await api.deleteProfile(profile.id);
                  removeProfile(profile.id);
                }, "")
              }
            >
              {save.busy ? "Removing…" : `Remove ${profile.name}`}
            </button>
            <button type="button" className="btn btn-secondary" disabled={save.busy} onClick={() => setMode("view")}>
              Keep
            </button>
          </div>
        </div>
      </li>
    );
  }

  return (
    <li>
      <div className="child-row">
        <div>
          <p className="rule-name">{profile.name}</p>
          <p className="chips">
            <span className="chip">Age {profile.age}</span>
            <span className="chip">{profile.policy_source === "template" ? "Age template" : "Custom rules"}</span>
          </p>
        </div>
        <div className="button-row">
          <button type="button" className="btn btn-quiet" onClick={startEdit}>
            Edit
          </button>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => {
              save.clear();
              setMode("confirm");
            }}
          >
            Remove
          </button>
        </div>
      </div>
    </li>
  );
}

function ChildrenSection() {
  const { profiles } = useFamily();
  const full = profiles.length >= MAX_KIDS;
  return (
    <section className="card" aria-labelledby="children-title">
      <h2 id="children-title">Children</h2>
      {profiles.length === 0 ? (
        <p>No children added yet.</p>
      ) : (
        <ul className="rule-list">
          {profiles.map((profile) => (
            <ChildRow key={profile.id} profile={profile} />
          ))}
        </ul>
      )}
      {full ? (
        <p className="hint">You have {MAX_KIDS} children, the maximum. Remove one to add another.</p>
      ) : (
        <Link className="btn btn-secondary btn-wide" to="/children/new">
          Add a child
        </Link>
      )}
    </section>
  );
}

export default function Profile() {
  return (
    <>
      <h1>Profile</h1>
      <AccountSection />
      <FamilySection />
      <ChildrenSection />
    </>
  );
}
