// A verdict is always shown as an icon AND a word, never colour alone.
const STATUS = {
  allow: { label: "Allow", className: "badge-allow", icon: "check" },
  ask_parent: { label: "Ask parent", className: "badge-ask", icon: "hand" },
  block: { label: "Block", className: "badge-block", icon: "block" },
};

function Icon({ name }) {
  const common = { width: 16, height: 16, viewBox: "0 0 16 16", "aria-hidden": true, focusable: "false" };
  if (name === "check") {
    return (
      <svg {...common}>
        <path d="M3 8.5l3.2 3.2L13 4.8" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
      </svg>
    );
  }
  if (name === "hand") {
    return (
      <svg {...common}>
        <path d="M8 2.5v7M8 12.6v.1" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" />
      </svg>
    );
  }
  return (
    <svg {...common}>
      <circle cx="8" cy="8" r="5.6" fill="none" stroke="currentColor" strokeWidth="1.8" />
      <path d="M4.2 11.8l7.6-7.6" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" />
    </svg>
  );
}

export default function StatusBadge({ status }) {
  const config = STATUS[status] ?? STATUS.ask_parent;
  return (
    <span className={`badge ${config.className}`}>
      <Icon name={config.icon} />
      {config.label}
    </span>
  );
}
