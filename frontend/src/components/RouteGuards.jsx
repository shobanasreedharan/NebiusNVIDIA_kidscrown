import { Navigate, Outlet } from "react-router-dom";
import { useAuth } from "../auth.jsx";
import Crown from "./Crown.jsx";
import { ErrorPanel, Spinner } from "./Feedback.jsx";

function FullScreen({ children }) {
  return (
    <div className="fullscreen">
      <Crown size={56} />
      {children}
    </div>
  );
}

// Everything behind sign-in. A saved session is verified before anything renders.
export function RequireAuth() {
  const { status, retry } = useAuth();
  if (status === "loading") {
    return (
      <FullScreen>
        <Spinner label="Signing you in" />
      </FullScreen>
    );
  }
  if (status === "unreachable") {
    return (
      <FullScreen>
        <ErrorPanel message="Can't reach KidsCrown. Check your connection and try again." onRetry={retry} />
      </FullScreen>
    );
  }
  if (status !== "authed") return <Navigate to="/login" replace />;
  return <Outlet />;
}

// Login and register: signed-in parents go straight to the app.
export function PublicOnly() {
  const { status } = useAuth();
  if (status === "authed") return <Navigate to="/" replace />;
  return <Outlet />;
}
