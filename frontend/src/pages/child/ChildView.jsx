import { useState } from "react";
import Tabs from "../../components/Tabs.jsx";
import { useQueue } from "../../queue.js";
import { useChannelRules } from "../../rules.js";
import AskParent from "./AskParent.jsx";
import CheckVideo from "./CheckVideo.jsx";
import Rules from "./Rules.jsx";

// Everything for one child. Every call below uses this child's id, so nothing
// on the page can drift to another child.
export default function ChildView({ profile }) {
  const [tab, setTab] = useState("check");
  const queue = useQueue(profile.id);
  const channelRules = useChannelRules(profile.id);
  const prefix = `child-${profile.id}`;

  const tabs = [
    { id: "check", label: "Check a video" },
    { id: "ask", label: "Ask parent", badge: queue.items.length },
    { id: "rules", label: "Rules" },
  ];

  return (
    <>
      <h1>{profile.name}</h1>
      <p className="chips">
        <span className="chip">Age {profile.age}</span>
        <span className="chip">{profile.policy_source === "template" ? "Age template" : "Custom rules"}</span>
      </p>

      <Tabs label={`${profile.name}'s screens`} tabs={tabs} active={tab} onChange={setTab} idPrefix={prefix} />

      {/* All three stay mounted so a result or typed rule isn't lost when switching tabs. */}
      <div role="tabpanel" id={`${prefix}-panel-check`} aria-labelledby={`${prefix}-tab-check`} hidden={tab !== "check"}>
        <CheckVideo profile={profile} queue={queue} channelRules={channelRules} onOpenQueue={() => setTab("ask")} />
      </div>
      <div role="tabpanel" id={`${prefix}-panel-ask`} aria-labelledby={`${prefix}-tab-ask`} hidden={tab !== "ask"}>
        <AskParent profile={profile} queue={queue} />
      </div>
      <div role="tabpanel" id={`${prefix}-panel-rules`} aria-labelledby={`${prefix}-tab-rules`} hidden={tab !== "rules"}>
        <Rules profile={profile} channelRules={channelRules} />
      </div>
    </>
  );
}
