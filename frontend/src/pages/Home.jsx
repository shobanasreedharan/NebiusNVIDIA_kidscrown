import { useState } from "react";
import { Link, Navigate } from "react-router-dom";
import { useFamily } from "../family.jsx";
import { useAuth } from "../auth.jsx";
import { ErrorPanel, PageSkeleton } from "../components/Feedback.jsx";
import QueueItem from "../components/QueueItem.jsx";
import { MAX_KIDS } from "../constants.js";
import { useFamilyQueues } from "../queue.js";
import ChildView from "./child/ChildView.jsx";

function policyLabel(profile) {
  return profile.policy_source === "template" ? "Age template" : "Custom rules";
}

function FamilyAskQueue({ queue }) {
  const [announcement, setAnnouncement] = useState("");

  function onResolved(item, decision) {
    queue.remove(item.id);
    const name = item.title || "The video";
    const who = item.childName ? ` for ${item.childName}` : "";
    setAnnouncement(decision === "approve" ? `Approved${who}: ${name}` : `Blocked${who}: ${name}`);
  }

  if (queue.state === "loading") return <PageSkeleton />;
  if (queue.state === "error") return <ErrorPanel message={queue.error} onRetry={() => queue.reload()} />;

  return (
    <section className="family-queue" aria-label="Ask parent across children">
      <h2>Ask parent</h2>
      <div className="sr-only" role="status" aria-live="polite">
        {announcement}
      </div>
      {announcement && (
        <p className="form-ok" aria-hidden="true">
          {announcement}
        </p>
      )}
      {queue.items.length === 0 ? (
        <div className="panel">
          <p>Nothing waiting. Unsure videos for any child show up here.</p>
        </div>
      ) : (
        <ul className="video-list">
          {queue.items.map((item) => (
            <QueueItem
              key={item.id}
              item={item}
              childName={item.childName}
              onResolved={onResolved}
            />
          ))}
        </ul>
      )}
    </section>
  );
}

function MainView() {
  const { user } = useAuth();
  const { profiles, setView } = useFamily();
  const familyQueue = useFamilyQueues(profiles);

  return (
    <>
      <h1>{user.family.name}</h1>
      <ul className="kid-grid">
        {profiles.map((profile) => {
          const pending = familyQueue.counts[profile.id] || 0;
          return (
            <li key={profile.id}>
              <button type="button" className="kid-card" onClick={() => setView(profile.id)}>
                <span className="kid-name">{profile.name}</span>
                <span className="kid-meta">
                  <span className="chip">Age {profile.age}</span>
                  <span className="chip">{policyLabel(profile)}</span>
                  {pending > 0 && <span className="chip chip-ask">{pending} to review</span>}
                </span>
              </button>
            </li>
          );
        })}
      </ul>
      {profiles.length < MAX_KIDS && (
        <Link className="btn btn-secondary" to="/children/new">
          Add a child
        </Link>
      )}
      <FamilyAskQueue queue={familyQueue} />
    </>
  );
}

// Main view or a child's view, depending on the top-bar switcher.
export default function Home() {
  const { state, error, reload, view, currentProfile, profiles } = useFamily();
  if (state === "idle" || state === "loading") return <PageSkeleton />;
  if (state === "error") return <ErrorPanel message={error} onRetry={reload} />;
  if (view !== "main" && currentProfile) return <ChildView key={currentProfile.id} profile={currentProfile} />;
  // A family with no children yet starts with onboarding.
  if (profiles.length === 0) return <Navigate to="/onboarding" replace />;
  return <MainView />;
}
