import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "./auth.jsx";
import { friendlyMessage } from "./api.js";

// A child's Ask Parent list. `remove` drops an item locally once it's resolved.
export function useQueue(profileId) {
  const { api } = useAuth();
  const [items, setItems] = useState([]);
  const [state, setState] = useState("loading"); // loading | ready | error
  const [error, setError] = useState("");
  const latest = useRef(0);

  const load = useCallback(
    async ({ silent = false } = {}) => {
      const request = ++latest.current;
      if (!silent) setState("loading");
      try {
        const data = await api.queue(profileId);
        if (request !== latest.current) return;
        setItems(data);
        setState("ready");
      } catch (err) {
        if (request !== latest.current) return;
        // A failed background refresh keeps what's already on screen.
        if (!silent) {
          setError(friendlyMessage(err));
          setState("error");
        }
      }
    },
    [api, profileId],
  );

  useEffect(() => {
    load();
    return () => {
      latest.current += 1;
    };
  }, [load]);

  const remove = useCallback((id) => setItems((list) => list.filter((item) => item.id !== id)), []);

  return { items, state, error, reload: load, remove };
}

// Merges every child's Ask Parent queue for the Main view (≤5 kids).
export function useFamilyQueues(profiles) {
  const { api } = useAuth();
  const [byProfile, setByProfile] = useState({});
  const [state, setState] = useState("loading"); // loading | ready | error
  const [error, setError] = useState("");
  const latest = useRef(0);
  const profileKey = profiles.map((p) => p.id).join(",");

  const load = useCallback(
    async ({ silent = false } = {}) => {
      const request = ++latest.current;
      if (!silent) setState("loading");
      try {
        const entries = await Promise.all(
          profiles.map(async (profile) => [profile.id, await api.queue(profile.id)]),
        );
        if (request !== latest.current) return;
        setByProfile(Object.fromEntries(entries));
        setState("ready");
      } catch (err) {
        if (request !== latest.current) return;
        if (!silent) {
          setError(friendlyMessage(err));
          setState("error");
        }
      }
    },
    // profileKey captures id membership; profiles themselves change identity often.
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [api, profileKey],
  );

  useEffect(() => {
    if (profiles.length === 0) {
      setByProfile({});
      setState("ready");
      return undefined;
    }
    load();
    return () => {
      latest.current += 1;
    };
  }, [load, profiles.length]);

  const remove = useCallback((id) => {
    setByProfile((current) => {
      const next = {};
      for (const [profileId, items] of Object.entries(current)) {
        next[profileId] = items.filter((item) => item.id !== id);
      }
      return next;
    });
  }, []);

  const counts = useMemo(() => {
    const map = {};
    for (const profile of profiles) {
      map[profile.id] = (byProfile[profile.id] || []).length;
    }
    return map;
  }, [profiles, byProfile]);

  const items = useMemo(() => {
    const nameById = Object.fromEntries(profiles.map((p) => [p.id, p.name]));
    return profiles
      .flatMap((profile) =>
        (byProfile[profile.id] || []).map((item) => ({
          ...item,
          childName: nameById[profile.id] || "Child",
        })),
      )
      .sort((a, b) => String(b.created_at || "").localeCompare(String(a.created_at || "")));
  }, [profiles, byProfile]);

  return { items, counts, state, error, reload: load, remove };
}
