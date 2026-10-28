import React from "react";

// Pulls the 11-character video ID out of common YouTube URL shapes.
export function extractVideoId(input) {
  if (!input) return null;
  if (/^[\w-]{11}$/.test(input)) return input;
  try {
    const url = new URL(input);
    if (url.hostname === "youtu.be") return url.pathname.slice(1, 12);
    if (url.searchParams.get("v")) return url.searchParams.get("v");
    const m = url.pathname.match(/\/(?:embed|shorts|live)\/([\w-]{11})/);
    return m ? m[1] : null;
  } catch {
    return null;
  }
}

// Usage: <YouTubeEmbed url={videoUrl} />  (or videoId="dQw4w9WgXcQ")
export default function YouTubeEmbed({ url, videoId, title = "YouTube video" }) {
  const id = videoId || extractVideoId(url);
  if (!id) return null;

  return (
    <div style={{ position: "relative", paddingTop: "56.25%", width: "100%" }}>
      <iframe
        src={`https://www.youtube-nocookie.com/embed/${id}?rel=0&modestbranding=1`}
        title={title}
        style={{ position: "absolute", inset: 0, width: "100%", height: "100%", border: 0, borderRadius: 12 }}
        allow="accelerometer; encrypted-media; picture-in-picture; fullscreen"
        allowFullScreen
        loading="lazy"
        referrerPolicy="strict-origin-when-cross-origin"
      />
    </div>
  );
}
