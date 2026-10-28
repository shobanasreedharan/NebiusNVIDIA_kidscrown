import { useNavigate } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import { useFamily } from "../family.jsx";

// Chooses whose view the whole app shows: the parent ("Main") or one child.
export default function ViewSwitcher() {
  const { user } = useAuth();
  const { profiles, state, view, setView } = useFamily();
  const navigate = useNavigate();

  const disabled = state !== "ready";

  function onChange(event) {
    setView(event.target.value);
    navigate("/");
  }

  return (
    <div className="switcher">
      <label htmlFor="view-select" className="sr-only">
        Viewing
      </label>
      <select id="view-select" value={view} onChange={onChange} disabled={disabled}>
        <option value="main">{user?.name} (Main)</option>
        {profiles.map((profile) => (
          <option key={profile.id} value={String(profile.id)}>
            {profile.name}
          </option>
        ))}
      </select>
    </div>
  );
}
