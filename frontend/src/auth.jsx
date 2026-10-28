import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState } from "react";
import { createApi } from "./api.js";
import { readStorage, writeStorage } from "./storage.js";

export const TOKEN_KEY = "kidscrown.token";
export const VIEW_KEY = "kidscrown.view";

const AuthContext = createContext(null);

// status: "loading" (checking a saved token) | "authed" | "anonymous" | "unreachable"
export function AuthProvider({ children, api: apiOverride }) {
  const [token, setToken] = useState(() => readStorage(TOKEN_KEY));
  const [user, setUser] = useState(null);
  const [status, setStatus] = useState(() => (readStorage(TOKEN_KEY) ? "loading" : "anonymous"));
  const tokenRef = useRef(token);
  tokenRef.current = token;

  const endSession = useCallback(() => {
    writeStorage(TOKEN_KEY, null);
    writeStorage(VIEW_KEY, null);
    tokenRef.current = null;
    setToken(null);
    setUser(null);
    setStatus("anonymous");
  }, []);

  const endSessionRef = useRef(endSession);
  endSessionRef.current = endSession;

  const api = useMemo(
    () =>
      apiOverride ||
      createApi({
        getToken: () => tokenRef.current,
        onUnauthorized: () => endSessionRef.current(),
      }),
    [apiOverride],
  );

  const startSession = useCallback((response) => {
    writeStorage(TOKEN_KEY, response.access_token);
    tokenRef.current = response.access_token;
    setToken(response.access_token);
    setUser(response.user);
    setStatus("authed");
  }, []);

  const checkSavedToken = useCallback(async () => {
    setStatus("loading");
    try {
      setUser(await api.me());
      setStatus("authed");
    } catch (error) {
      if (error.status === 401) endSessionRef.current();
      else setStatus("unreachable");
    }
  }, [api]);

  useEffect(() => {
    if (readStorage(TOKEN_KEY)) checkSavedToken();
    // Runs once on load: a saved token is verified before the app trusts it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const login = useCallback(async (email, password) => {
    startSession(await api.login({ email, password }));
  }, [api, startSession]);

  const register = useCallback(async (payload) => {
    startSession(await api.register(payload));
  }, [api, startSession]);

  const value = useMemo(
    () => ({ status, user, token, api, login, register, logout: endSession, updateUser: setUser, retry: checkSavedToken }),
    [status, user, token, api, login, register, endSession, checkSavedToken],
  );

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth() {
  const context = useContext(AuthContext);
  if (!context) throw new Error("useAuth must be used inside <AuthProvider>");
  return context;
}
