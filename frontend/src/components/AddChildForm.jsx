import { useRef, useState } from "react";
import { useAuth } from "../auth.jsx";
import { useFamily } from "../family.jsx";
import { friendlyMessage } from "../api.js";
import Field from "./Field.jsx";
import { Spinner } from "./Feedback.jsx";
import { AGES, MAX_CHILD_POLICY_CHARS } from "../constants.js";

// Name and age, then the rules: choosing the age fills in a suggested set the
// parent can keep or change. Rules the parent has edited are never overwritten
// by a new age's suggestion.
export default function AddChildForm({ onAdded, onCancel, submitLabel = "Add child" }) {
  const { api } = useAuth();
  const { addProfile } = useFamily();
  const [name, setName] = useState("");
  const [age, setAge] = useState("");
  const [text, setText] = useState("");
  const [template, setTemplate] = useState(null); // the suggestion last loaded: { band, policy_text }
  const [templateState, setTemplateState] = useState("idle"); // idle | loading | ready | error
  const [note, setNote] = useState("");
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  // Refs let the async template load see the latest text without stale closures.
  const textRef = useRef(text);
  textRef.current = text;
  const templateRef = useRef(template);
  templateRef.current = template;
  const latest = useRef(0);

  async function loadTemplate(nextAge, { overwrite = false } = {}) {
    const request = ++latest.current;
    setTemplateState("loading");
    setNote("");
    setError("");
    try {
      const loaded = await api.getTemplate(nextAge);
      if (request !== latest.current) return;
      const previous = templateRef.current;
      const edited = previous && textRef.current.trim() !== previous.policy_text.trim();
      if (overwrite || !edited) {
        setText(loaded.policy_text);
      } else {
        setNote("Your edits are kept. Choose “Use the age template” to load the new suggestion instead.");
      }
      setTemplate(loaded);
      setTemplateState("ready");
    } catch (err) {
      if (request !== latest.current) return;
      setError(friendlyMessage(err));
      setTemplateState("error");
    }
  }

  function onAgeChange(event) {
    const value = event.target.value;
    setAge(value);
    if (value) loadTemplate(Number(value));
  }

  async function submit(event) {
    event.preventDefault();
    setBusy(true);
    setError("");
    try {
      const created = await api.createProfile({ name: name.trim(), age: Number(age), policy_text: text.trim() });
      addProfile(created);
      setName("");
      setAge("");
      setText("");
      setTemplate(null);
      setTemplateState("idle");
      setNote("");
      onAdded?.(created);
    } catch (err) {
      setError(friendlyMessage(err));
    } finally {
      setBusy(false);
    }
  }

  const who = name.trim() || "this child";
  const ready = name.trim() && age && text.trim() && templateState !== "loading";

  return (
    <form className="form" onSubmit={submit} noValidate>
      <Field
        label="Name or nickname"
        hint="A first name or nickname is enough."
        autoComplete="off"
        maxLength={120}
        value={name}
        onChange={(event) => setName(event.target.value)}
      />
      <div className="field">
        <label htmlFor="child-age">Age</label>
        <select id="child-age" value={age} onChange={onAgeChange}>
          <option value="">Choose an age</option>
          {AGES.map((value) => (
            <option key={value} value={value}>
              {value}
            </option>
          ))}
        </select>
      </div>

      {age && templateState === "loading" && !text && <Spinner label="Loading suggested rules" />}

      {age && (text || templateState === "ready") && (
        <>
          <Field
            as="textarea"
            rows={10}
            label={`Rules for ${who}`}
            hint={`${template ? `Suggested for ages ${template.band}. ` : ""}Keep them or change them however you like. Your family rules also apply, and these win if they disagree.`}
            maxLength={MAX_CHILD_POLICY_CHARS}
            value={text}
            onChange={(event) => setText(event.target.value)}
          />
          {note && <p className="hint" role="status">{note}</p>}
          {template && text.trim() !== template.policy_text.trim() && (
            <button
              type="button"
              className="btn btn-quiet"
              onClick={() => {
                setText(template.policy_text);
                setNote("");
              }}
            >
              Use the age template
            </button>
          )}
        </>
      )}

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      <div className="button-row">
        <button type="submit" className="btn btn-primary" disabled={busy || !ready}>
          {busy ? "Adding…" : submitLabel}
        </button>
        {onCancel && (
          <button type="button" className="btn btn-secondary" onClick={onCancel}>
            Cancel
          </button>
        )}
      </div>
    </form>
  );
}
