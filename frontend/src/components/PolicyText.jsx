// Shows a policy as readable paragraphs: one per line, so "Allow: … / Ask
// parent: … / Block: …" doesn't run together.
export default function PolicyText({ text, empty = "No rules yet." }) {
  const lines = (text || "").split("\n").map((line) => line.trim()).filter(Boolean);
  if (lines.length === 0) return <p className="hint">{empty}</p>;
  return (
    <div className="policy-text">
      {lines.map((line, index) => (
        <p key={index}>{line}</p>
      ))}
    </div>
  );
}
