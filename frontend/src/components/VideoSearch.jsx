import { useState } from "react";
import { friendlyMessage } from "../api.js";
import "./VideoSearch.css";

// A search box that behaves like DemoStrip: picking a result hands its link to
// the parent screen, which runs the normal check (verdict, queue, channel rules).
// Props: search(query) -> Promise<result[]>, onPick(link, video), disabled.
export default function VideoSearch({ search, onPick, disabled = false }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState([]);
  const [searching, setSearching] = useState(false);
  const [error, setError] = useState("");
  const [searched, setSearched] = useState(false);

  async function onSubmit(event) {
    event.preventDefault();
    const q = query.trim();
    if (q.length < 2) return;
    setSearching(true);
    setError("");
    try {
      setResults(await search(q));
      setSearched(true);
    } catch (err) {
      setError(friendlyMessage(err));
    } finally {
      setSearching(false);
    }
  }

  return (
    <section className="vs" aria-label="Search videos">
      <form className="vs-bar" onSubmit={onSubmit} role="search" noValidate>
        <label htmlFor="video-search" className="vs-sr">
          Search for a video
        </label>
        <span className="vs-icon" aria-hidden="true">
          🔍
        </span>
        <input
          id="video-search"
          type="search"
          autoComplete="off"
          placeholder="What do you want to watch?"
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        <button type="submit" disabled={searching || disabled || query.trim().length < 2}>
          {searching ? "Looking…" : "Search"}
        </button>
      </form>

      {error && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}
      {searching && <p className="vs-status">Finding videos… 🎬</p>}
      {searched && !searching && !error && results.length === 0 && (
        <p className="vs-status">No videos found. Try different words! 🙂</p>
      )}

      {results.length > 0 && (
        <ul className="vs-grid">
          {results.map((video) => (
            <li key={video.video_id}>
              <button
                type="button"
                className="vs-card"
                disabled={disabled}
                onClick={() => onPick(`https://www.youtube.com/watch?v=${video.video_id}`, video)}
              >
                <span className="vs-thumb">
                  {video.thumbnail && <img src={video.thumbnail} alt="" loading="lazy" />}
                  <span className="vs-play" aria-hidden="true">
                    ▶
                  </span>
                </span>
                <span className="vs-title">{video.title}</span>
                <span className="vs-channel">{video.channel}</span>
              </button>
            </li>
          ))}
        </ul>
      )}
    </section>
  );
}
