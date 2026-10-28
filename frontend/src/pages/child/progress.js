import { useEffect, useState } from "react";

// What to tell a parent while a check is running. The wording doesn't claim a
// stage the server hasn't reported; it only says the check is still going and
// how long the slow ones take (the server gives a transcript up to 20 seconds).
export function progressLabel(seconds) {
  if (seconds < 8) return "Checking the video";
  if (seconds < 20) return "Still working. Reading a video's transcript can take a little while";
  return "Still working. Some videos take up to 30 seconds";
}

// Whole seconds since `active` became true; back to 0 when it stops.
export function useElapsedSeconds(active) {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!active) {
      setSeconds(0);
      return undefined;
    }
    const started = Date.now();
    const timer = setInterval(() => setSeconds(Math.floor((Date.now() - started) / 1000)), 1000);
    return () => clearInterval(timer);
  }, [active]);
  return seconds;
}
