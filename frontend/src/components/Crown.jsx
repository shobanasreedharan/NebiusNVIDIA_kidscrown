// The KidsCrown mark: a crown whose three jewels are the three answers a
// video can get — green (allow), amber (ask parent), red (block).
export default function Crown({ size = 40, title }) {
  return (
    <svg
      width={size}
      height={size * 0.83}
      viewBox="0 0 48 40"
      role={title ? "img" : undefined}
      aria-label={title}
      aria-hidden={title ? undefined : true}
      focusable="false"
    >
      <path d="M5 35V17l9 7 10-14 10 14 9-7v18z" fill="#f2b62e" />
      <rect x="5" y="35" width="38" height="4" rx="2" fill="#c98f0a" />
      <circle cx="5" cy="14" r="4.2" fill="#22c55e" />
      <circle cx="24" cy="7" r="4.2" fill="#fbbf24" />
      <circle cx="43" cy="14" r="4.2" fill="#ef4444" />
    </svg>
  );
}
