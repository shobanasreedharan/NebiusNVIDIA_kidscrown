import StatusBadge from "./StatusBadge.jsx";
import { DEMO_VIDEOS, demoUrl } from "../demoVideos.js";

// Quick picks that fill the Check a video form. Real classification still runs.
export default function DemoStrip({ onPick, disabled }) {
  return (
    <section className="demo-strip" aria-label="Try an example video">
      <h2>Try an example</h2>
      <p className="hint">
        Tap one to paste its link, then check it. Labels are typical outcomes, not guarantees.
      </p>
      <ul className="demo-grid">
        {DEMO_VIDEOS.map((video) => (
          <li key={video.id}>
            <button
              type="button"
              className={`demo-card${video.adversarial ? " demo-card-warn" : ""}`}
              disabled={disabled}
              onClick={() => onPick(demoUrl(video.id))}
            >
              <span className="demo-card-top">
                <StatusBadge status={video.expected} />
                {video.adversarial && <span className="demo-limit">Limitation</span>}
              </span>
              <span className="demo-label">{video.label}</span>
              <span className="demo-note">{video.note}</span>
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
