import { useState } from "react";
import { ErrorPanel, PageSkeleton } from "../../components/Feedback.jsx";
import QueueItem from "../../components/QueueItem.jsx";

// Videos the AI wasn't sure about. Approve or block, with an optional note.
export default function AskParent({ profile, queue }) {
  const [announcement, setAnnouncement] = useState("");

  function onResolved(item, decision) {
    queue.remove(item.id);
    const name = item.title || "The video";
    setAnnouncement(decision === "approve" ? `Approved: ${name}` : `Blocked: ${name}`);
  }

  if (queue.state === "loading") return <PageSkeleton />;
  if (queue.state === "error") return <ErrorPanel message={queue.error} onRetry={() => queue.reload()} />;

  return (
    <div>
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
          <h2>Nothing to review</h2>
          <p>When the AI isn't sure about a video for {profile.name}, it shows up here for you to approve or block.</p>
        </div>
      ) : (
        <ul className="video-list">
          {queue.items.map((item) => (
            <QueueItem key={item.id} item={item} onResolved={onResolved} />
          ))}
        </ul>
      )}
    </div>
  );
}
