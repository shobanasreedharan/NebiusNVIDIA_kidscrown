import { useState } from "react";
import { Link } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import { friendlyMessage } from "../api.js";
import AuthLayout from "../components/AuthLayout.jsx";
import Field from "../components/Field.jsx";
import { MAX_FAMILY_POLICY_CHARS } from "../constants.js";

export default function Register() {
  const { register } = useAuth();
  const [form, setForm] = useState({
    parent_name: "",
    email: "",
    password: "",
    family_name: "",
    family_policy_text: "",
  });
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);

  const set = (key) => (event) => setForm((current) => ({ ...current, [key]: event.target.value }));
  const ready = form.parent_name.trim() && form.email.trim() && form.password && form.family_name.trim();

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setBusy(true);
    try {
      await register({
        ...form,
        parent_name: form.parent_name.trim(),
        family_name: form.family_name.trim(),
        family_policy_text: form.family_policy_text.trim(),
      });
    } catch (err) {
      setError(friendlyMessage(err));
      setBusy(false);
    }
  }

  return (
    <AuthLayout>
      <form className="card form" onSubmit={onSubmit} noValidate>
        <h2>Create your family account</h2>
        <Field label="Your name" autoComplete="name" required value={form.parent_name} onChange={set("parent_name")} />
        <Field
          label="Email"
          type="email"
          autoComplete="email"
          inputMode="email"
          required
          value={form.email}
          onChange={set("email")}
        />
        <Field
          label="Password"
          type="password"
          autoComplete="new-password"
          hint="At least 8 characters. Use one you don't use anywhere else."
          required
          value={form.password}
          onChange={set("password")}
        />
        <Field label="Family name" required value={form.family_name} onChange={set("family_name")} />
        <Field
          as="textarea"
          rows={4}
          label="Family rules (optional)"
          hint="Rules for every child. You can set rules for each child separately later."
          placeholder="No scary or violent content. Educational videos are always welcome."
          maxLength={MAX_FAMILY_POLICY_CHARS}
          value={form.family_policy_text}
          onChange={set("family_policy_text")}
        />
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" className="btn btn-primary" disabled={busy || !ready}>
          {busy ? "Creating account…" : "Create account"}
        </button>
        <p className="form-switch">
          Already have an account? <Link to="/login">Sign in</Link>
        </p>
      </form>
    </AuthLayout>
  );
}
