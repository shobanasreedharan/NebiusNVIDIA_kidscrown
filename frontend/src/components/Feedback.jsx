import { useEffect, useRef } from "react";

export function Spinner({ label = "Loading" }) {
  return (
    <div className="center-note" role="status">
      <span className="spinner" aria-hidden="true" />
      <span>{label}…</span>
    </div>
  );
}

export function PageSkeleton() {
  return (
    <div className="skeleton-stack" role="status" aria-label="Loading">
      <div className="skeleton skeleton-title" />
      <div className="skeleton skeleton-card" />
      <div className="skeleton skeleton-card" />
    </div>
  );
}

// Shown when something failed and the parent can retry. Focus moves here so
// keyboard and screen-reader users notice it.
export function ErrorPanel({ message, onRetry }) {
  const ref = useRef(null);
  useEffect(() => {
    ref.current?.focus();
  }, []);
  return (
    <div className="panel panel-error" role="alert" tabIndex={-1} ref={ref}>
      <p>{message}</p>
      {onRetry && (
        <button type="button" className="btn btn-secondary" onClick={onRetry}>
          Try again
        </button>
      )}
    </div>
  );
}
