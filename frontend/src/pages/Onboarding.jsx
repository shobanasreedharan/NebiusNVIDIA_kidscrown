import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import { useFamily } from "../family.jsx";
import { friendlyMessage } from "../api.js";
import AddChildForm from "../components/AddChildForm.jsx";
import Field from "../components/Field.jsx";
import { MAX_FAMILY_POLICY_CHARS, MAX_KIDS } from "../constants.js";

// First sign-in: confirm the family rules, then add the children.
export default function Onboarding() {
  const { api, user, updateUser } = useAuth();
  const { profiles, setView } = useFamily();
  const navigate = useNavigate();
  const [step, setStep] = useState("family");
  const [familyName, setFamilyName] = useState(user.family.name);
  const [familyRules, setFamilyRules] = useState(user.family.policy_text);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function continueToChildren(event) {
    event.preventDefault();
    setError("");
    const changed = familyName.trim() !== user.family.name || familyRules.trim() !== user.family.policy_text.trim();
    if (!changed) {
      setStep("children");
      return;
    }
    setBusy(true);
    try {
      const updated = await api.updateFamily({ name: familyName.trim(), policy_text: familyRules.trim() });
      updateUser({ ...user, family: updated });
      setStep("children");
    } catch (err) {
      setError(friendlyMessage(err));
    } finally {
      setBusy(false);
    }
  }

  function finish() {
    setView("main");
    navigate("/", { replace: true });
  }

  const full = profiles.length >= MAX_KIDS;

  if (step === "family") {
    return (
      <>
        <p className="step">Step 1 of 2</p>
        <h1>Welcome, {user.name}</h1>
        <p>These rules apply to every child in your family. You can change them any time from your profile.</p>
        <form className="card form" onSubmit={continueToChildren} noValidate>
          <Field label="Family name" maxLength={120} value={familyName} onChange={(event) => setFamilyName(event.target.value)} />
          <Field
            as="textarea"
            rows={7}
            label="Family rules"
            hint="Plain language is fine. Each child can also have their own rules."
            placeholder="No scary or violent content. Educational videos are always welcome."
            maxLength={MAX_FAMILY_POLICY_CHARS}
            value={familyRules}
            onChange={(event) => setFamilyRules(event.target.value)}
          />
          {error && (
            <p className="form-error" role="alert">
              {error}
            </p>
          )}
          <button type="submit" className="btn btn-primary" disabled={busy || !familyName.trim()}>
            {busy ? "Saving…" : "Continue"}
          </button>
        </form>
      </>
    );
  }

  return (
    <>
      <p className="step">Step 2 of 2</p>
      <h1>Add your children</h1>
      <p>You can add up to {MAX_KIDS}. Each child gets rules based on their age, which you can change.</p>

      {profiles.length > 0 && (
        <section className="card" aria-labelledby="added-title">
          <h2 id="added-title">Added so far</h2>
          <ul className="plain-list">
            {profiles.map((profile) => (
              <li key={profile.id}>
                {profile.name}, age {profile.age}
              </li>
            ))}
          </ul>
        </section>
      )}

      {!full ? (
        <section className="card" aria-labelledby="add-title">
          <h2 id="add-title">{profiles.length === 0 ? "Your first child" : "Add another child"}</h2>
          <AddChildForm submitLabel="Add child" />
        </section>
      ) : (
        <p className="hint">You've added the maximum of {MAX_KIDS} children.</p>
      )}

      <button type="button" className="btn btn-primary btn-wide" disabled={profiles.length === 0} onClick={finish}>
        Finish
      </button>
    </>
  );
}
