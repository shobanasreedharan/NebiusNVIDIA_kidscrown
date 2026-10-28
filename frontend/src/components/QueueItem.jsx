import { useState } from "react";
import { useAuth } from "../auth.jsx";
import { friendlyMessage } from "../api.js";
import VideoCard from "./VideoCard.jsx";

// One Ask Parent card: optional child label, optional note, Approve / Block.
export default function QueueItem({ item, childName, onResolved }) {
  const { api } = useAuth();
  const [noteOpen, setNoteOpen] = useState(false);
  const [comment, setComment] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function decide(decision) {
    setBusy(true);
    setError("");
    try {
      await api.resolve(item.id, { decision, comment: comment.trim() || null });
      onResolved(item, decision);
    } catch (err) {
      setError(friendlyMessage(err));
      setBusy(false);
    }
  }

  return (
    <li>
      <VideoCard item={item}>
        {childName && <p className="video-child-label">For {childName}</p>}
        {noteOpen ? (
          <div className="field">
            <label htmlFor={`note-${item.id}`}>Note (optional)</label>
            <textarea
              id={`note-${item.id}`}
              rows={2}
              maxLength={500}
              placeholder="Why? KidsCrown uses your notes to judge similar videos."
              value={comment}
              onChange={(event) => setComment(event.target.value)}
            />
          </div>
        ) : (
          <button type="button" className="btn btn-quiet" onClick={() => setNoteOpen(true)}>
            Add a note
          </button>
        )}
        <div className="button-row">
          <button type="button" className="btn btn-allow" disabled={busy} onClick={() => decide("approve")}>
            Approve
          </button>
          <button type="button" className="btn btn-block" disabled={busy} onClick={() => decide("block")}>
            Block
          </button>
        </div>
        {error && (
          <p className="form-error" role="alert">
            {error}
          </p>
        )}
      </VideoCard>
    </li>
  );
}
