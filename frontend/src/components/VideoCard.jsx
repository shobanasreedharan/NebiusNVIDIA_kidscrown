import { useState } from "react";
import StatusBadge from "./StatusBadge.jsx";

const thumbnailUrl = (videoId) => `https://i.ytimg.com/vi/${videoId}/mqdefault.jpg`;
const watchUrl = (videoId) => `https://www.youtube.com/watch?v=${videoId}`;

// One judged video: what it is, the verdict and why, and (as children) what
// the parent can do about it. Videos never play here; "Open on YouTube" goes
// to YouTube in a new tab.
export default function VideoCard({ item, children }) {
  const blocked = item.status === "block";
  const [showPreview, setShowPreview] = useState(!blocked);
  const [imageFailed, setImageFailed] = useState(false);
  const percent = Math.round((item.confidence ?? 0) * 100);

  return (
    <article className={`video video-${item.status}`}>
      <div className="video-media">
        {showPreview && !imageFailed ? (
          <img
            src={thumbnailUrl(item.video_id)}
            alt=""
            loading="lazy"
            onError={() => setImageFailed(true)}
          />
        ) : (
          <div className="video-media-empty">
            {blocked && !showPreview ? (
              <>
                <p>Preview hidden because this video is blocked.</p>
                <button type="button" className="btn btn-quiet" onClick={() => setShowPreview(true)}>
                  Show preview
                </button>
              </>
            ) : (
              <p>No preview available.</p>
            )}
          </div>
        )}
      </div>

      <div className="video-body">
        <div className="video-head">
          <StatusBadge status={item.status} />
          <span className="video-confidence">
            {item.decided_by_channel_rule ? "Your channel rule" : `Confidence ${percent}%`}
          </span>
        </div>

        <h3 className="video-title">{item.title || "Video details unavailable"}</h3>
        {item.channel_name && <p className="video-channel">{item.channel_name}</p>}

        <p className="video-explanation">{item.explanation}</p>

        <p className="video-open">
          <a href={watchUrl(item.video_id)} target="_blank" rel="noopener noreferrer">
            Open on YouTube<span className="sr-only"> (opens in a new tab)</span>
          </a>
        </p>

        {children}
      </div>
    </article>
  );
}
