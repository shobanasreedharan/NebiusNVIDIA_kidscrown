import { createContext, useCallback, useContext, useEffect, useMemo, useState } from "react";
import { useAuth, VIEW_KEY } from "./auth.jsx";
import { friendlyMessage } from "./api.js";
import { readStorage, writeStorage } from "./storage.js";

const FamilyContext = createContext(null);

// Holds the family's children and which view is selected. `view` is "main"
// (the parent) or a child's id as a string. Pages read `currentProfile` and
// scope everything they show and fetch to it.
export function FamilyProvider({ children }) {
  const { status, api } = useAuth();
  const [profiles, setProfiles] = useState([]);
  const [state, setState] = useState("idle"); // idle | loading | ready | error
  const [error, setError] = useState(null);
  const [savedView, setSavedView] = useState(() => readStorage(VIEW_KEY) || "main");

  // `silent` refreshes the list without swapping the page for a loading state,
  // so a child's screen keeps its place (tab, typed text) while data refreshes.
  const reload = useCallback(async ({ silent = false } = {}) => {
    if (!silent) setState("loading");
    setError(null);
    try {
      setProfiles(await api.listProfiles());
      setState("ready");
    } catch (err) {
      setError(friendlyMessage(err));
      setState("error");
    }
  }, [api]);

  useEffect(() => {
    if (status === "authed") {
      reload();
    } else if (status === "anonymous") {
      // Only a real sign-out clears the selection; while a saved session is
      // still being verified ("loading") the remembered view is kept.
      setProfiles([]);
      setState("idle");
      setSavedView("main");
    }
  }, [status, reload]);

  const currentProfile = useMemo(
    () => (savedView === "main" ? null : profiles.find((p) => String(p.id) === savedView) ?? null),
    [savedView, profiles],
  );

  // A saved child that no longer exists (deleted, or another family's browser
  // state) falls back to the main view once the list has loaded.
  const view = state === "ready" && savedView !== "main" && !currentProfile ? "main" : savedView;

  const addProfile = useCallback((created) => {
    setProfiles((list) => (list.some((p) => p.id === created.id) ? list : [...list, created]));
  }, []);

  const removeProfile = useCallback((id) => {
    setProfiles((list) => list.filter((profile) => profile.id !== id));
  }, []);

  // After the parent edits a child, swap in the saved version without a reload.
  const replaceProfile = useCallback((updated) => {
    setProfiles((list) => list.map((profile) => (profile.id === updated.id ? updated : profile)));
  }, []);

  const setView = useCallback((next) => {
    const value = next === "main" ? "main" : String(next);
    setSavedView(value);
    writeStorage(VIEW_KEY, value);
  }, []);

  const value = useMemo(
    () => ({ profiles, state, error, reload, addProfile, removeProfile, replaceProfile, view, setView, currentProfile }),
    [profiles, state, error, reload, addProfile, removeProfile, replaceProfile, view, setView, currentProfile],
  );

  return <FamilyContext.Provider value={value}>{children}</FamilyContext.Provider>;
}

export function useFamily() {
  const context = useContext(FamilyContext);
  if (!context) throw new Error("useFamily must be used inside <FamilyProvider>");
  return context;
}
