import { useEffect, useRef, useState } from "react";
import { useAuth } from "../../auth.jsx";
import { friendlyMessage } from "../../api.js";
import DemoStrip from "../../components/DemoStrip.jsx";
import { Spinner } from "../../components/Feedback.jsx";
import VideoCard from "../../components/VideoCard.jsx";
import VideoSearch from "../../components/VideoSearch.jsx";
import YouTubeEmbed from "../../components/YouTubeEmbed.jsx";
import { progressLabel, useElapsedSeconds } from "./progress.js";

const BAD_LINK_MESSAGE = "That doesn't look like a YouTube video link. Copy it from the video's Share button.";

// The backend's own wording for an unusable link is written for developers.
function checkErrorMessage(error) {
  if (error?.status === 400 && /youtube video id/i.test(error.message)) return BAD_LINK_MESSAGE;
  return friendlyMessage(error);
}

// Search for a video or paste a link, get the verdict. A channel with a rule is
// decided on the server before any AI call, so those answers come back almost at once.
export default function CheckVideo({ profile, queue, channelRules, onOpenQueue }) {
  const { api } = useAuth();
  const [url, setUrl] = useState("");
  const [phase, setPhase] = useState("idle"); // idle | checking | done | error
  const [error, setError] = useState("");
  const [result, setResult] = useState(null);
  const [ruleBusy, setRuleBusy] = useState(false);
  const [ruleMessage, setRuleMessage] = useState("");
  const [ruleError, setRuleError] = useState("");
  const [notice, setNotice] = useState("");
  const resultRef = useRef(null);
  const controllerRef = useRef(null);
  const elapsed = useElapsedSeconds(phase === "checking");

  useEffect(() => {
    if (phase === "done") resultRef.current?.focus();
  }, [phase, result]);

  // Leaving this child's screen cancels a check that is still waiting.
  useEffect(() => () => controllerRef.current?.abort(), []);

  // Runs one check for a link. Used by the form, and by tapping a search result.
  async function runCheck(link) {
    const controller = new AbortController();
    controllerRef.current = controller;
    setPhase("checking");
    setError("");
    setNotice("");
    setResult(null);
    setRuleMessage("");
    setRuleError("");
    try {
      const checked = await api.classify(profile.id, link, { signal: controller.signal });
      setResult(checked);
      setPhase("done");
      if (checked.status === "ask_parent") queue.reload({ silent: true });
    } catch (err) {
      if (err?.name === "AbortError") {
        setNotice("Cancelled. The check may still finish in the background, so checking the same link again should be quick.");
        setPhase("idle");
        return;
      }
      setError(checkErrorMessage(err));
      setPhase("error");
    }
  }

  function onSubmit(event) {
    event.preventDefault();
    const link = url.trim();
    if (!link) return;
    runCheck(link);
  }

  function cancel() {
    controllerRef.current?.abort();
  }

  function pickDemo(link) {
    setUrl(link);
    setNotice("Example link pasted. Tap Check video to run it.");
    setPhase("idle");
    setError("");
    setResult(null);
  }

  // Tapping a search result checks it right away.
  function pickSearchResult(link) {
    setUrl(link);
    runCheck(link);
  }

  async function addRule(action) {
    setRuleBusy(true);
    setRuleError("");
    setRuleMessage("");
    try {
      await channelRules.add({
        channel_id: result.channel_id,
        channel_name: result.channel_name || undefined,
        action,
      });
      const name = result.channel_name || "this channel";
      setRuleMessage(
        `Done. Videos from ${name} will now always be ${action === "allow" ? "allowed" : "blocked"} for ${profile.name}.`,
      );
    } catch (err) {
      setRuleError(friendlyMessage(err));
    } finally {
      setRuleBusy(false);
    }
  }

  async function removeRule() {
    setRuleBusy(true);
    setRuleError("");
    setRuleMessage("");
    try {
      await channelRules.remove(rule);
      setRuleMessage("Rule removed. This channel's videos will be checked one by one again.");
    } catch (err) {
      setRuleError(friendlyMessage(err));
    } finally {
      setRuleBusy(false);
    }
  }

  const rule = result?.channel_id ? channelRules.rules.find((item) => item.channel_id === result.channel_id) ?? null : null;
  const channelName = result?.channel_name || "this channel";

  return (
    <div>
      <VideoSearch search={(q) => api.search(q)} onPick={pickSearchResult} disabled={phase === "checking"} />

      <form className="check-form" onSubmit={onSubmit} noValidate>
        <div className="field">
          <label htmlFor="video-link">Or paste a YouTube link</label>
          <input
            id="video-link"
            type="text"
            inputMode="url"
            autoComplete="off"
            autoCapitalize="none"
            spellCheck={false}
            placeholder="https://www.youtube.com/watch?v=…"
            value={url}
            onFocus={(event) => event.target.select()} // pasting a new link replaces the old one
            onChange={(event) => setUrl(event.target.value)}
          />
        </div>
        <button type="submit" className="btn btn-primary" disabled={phase === "checking" || !url.trim()}>
          {phase === "checking" ? "Checking…" : "Check video"}
        </button>
      </form>

      <DemoStrip onPick={pickDemo} disabled={phase === "checking"} />

      <div aria-live="polite">
        {phase === "checking" && (
          <>
            <Spinner label={progressLabel(elapsed)} />
            <button type="button" className="btn btn-quiet" onClick={cancel}>
              Cancel
            </button>
          </>
        )}
      </div>

      {phase === "error" && (
        <p className="form-error" role="alert">
          {error}
        </p>
      )}

      {phase === "idle" && (
        <p className="hint">
          {notice || `Search or paste a link to see whether ${profile.name}'s rules allow it, need your say-so, or block it.`}
        </p>
      )}

      {phase === "done" && result && (
        <section className="result" aria-label="Result" tabIndex={-1} ref={resultRef}>
          <VideoCard item={result}>
            {result.status === "allow" && result.video_id && (
              <div style={{ margin: "12px 0" }}>
                <YouTubeEmbed videoId={result.video_id} title={result.title || "YouTube video"} />
              </div>
            )}

            {result.status === "ask_parent" && (
              <div className="inline-note">
                <p>This video is in your Ask parent list.</p>
                <button type="button" className="btn btn-secondary" onClick={onOpenQueue}>
                  Review it now
                </button>
              </div>
            )}

            {result.channel_id && (
              <div className="channel-rule">
                {rule ? (
                  <>
                    <p>
                      Rule for {channelName}: <strong>always {rule.action === "allow" ? "allow" : "block"}</strong>
                    </p>
                    <button type="button" className="btn btn-quiet" disabled={ruleBusy} onClick={removeRule}>
                      Remove rule
                    </button>
                  </>
                ) : (
                  <>
                    <p>Treat every video from {channelName} the same way?</p>
                    <div className="button-row">
                      <button type="button" className="btn btn-secondary" disabled={ruleBusy} onClick={() => addRule("allow")}>
                        Always allow this channel
                      </button>
                      <button type="button" className="btn btn-secondary" disabled={ruleBusy} onClick={() => addRule("block")}>
                        Always block this channel
                      </button>
                    </div>
                  </>
                )}
                {ruleMessage && (
                  <p className="form-ok" role="status">
                    {ruleMessage}
                  </p>
                )}
                {ruleError && (
                  <p className="form-error" role="alert">
                    {ruleError}
                  </p>
                )}
              </div>
            )}
          </VideoCard>
        </section>
      )}
    </div>
  );
}
