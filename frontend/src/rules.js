import { useCallback, useEffect, useRef, useState } from "react";
import { useAuth } from "./auth.jsx";
import { friendlyMessage } from "./api.js";

// One child's channel rules, shared by the Check a video and Rules tabs so a
// rule added in one place shows up in the other immediately. The write helpers
// throw on failure; the caller decides how to show it.
export function useChannelRules(profileId) {
  const { api } = useAuth();
  const [rules, setRules] = useState([]);
  const [state, setState] = useState("loading"); // loading | ready | error
  const [error, setError] = useState("");
  const latest = useRef(0);

  const load = useCallback(async () => {
    const request = ++latest.current;
    try {
      const data = await api.listRules(profileId);
      if (request !== latest.current) return;
      setRules(data);
      setState("ready");
    } catch (err) {
      if (request !== latest.current) return;
      setError(friendlyMessage(err));
      setState("error");
    }
  }, [api, profileId]);

  useEffect(() => {
    load();
    return () => {
      latest.current += 1;
    };
  }, [load]);

  const add = useCallback(
    async (payload) => {
      const created = await api.createRule(profileId, payload);
      setRules((list) => [created, ...list.filter((rule) => rule.channel_id !== created.channel_id)]);
      return created;
    },
    [api, profileId],
  );

  const remove = useCallback(
    async (rule) => {
      await api.deleteRule(profileId, rule.id);
      setRules((list) => list.filter((item) => item.id !== rule.id));
    },
    [api, profileId],
  );

  // The backend keeps one rule per channel, so changing a rule means delete, then create.
  const switchAction = useCallback(
    async (rule) => {
      try {
        await api.deleteRule(profileId, rule.id);
        const created = await api.createRule(profileId, {
          channel_id: rule.channel_id,
          channel_name: rule.channel_name || undefined,
          action: rule.action === "allow" ? "block" : "allow",
          reason: rule.reason || undefined,
        });
        setRules((list) => [created, ...list.filter((item) => item.id !== rule.id)]);
      } catch (err) {
        await load(); // the delete may have gone through; show what's really there
        throw err;
      }
    },
    [api, profileId, load],
  );

  return { rules, state, error, reload: load, add, remove, switchAction };
}
