import Crown from "./Crown.jsx";
import StatusBadge from "./StatusBadge.jsx";

const EXAMPLES = [
  { status: "allow", title: "Dinosaur facts for curious kids", reason: "Science and animals are on your welcome list." },
  { status: "ask_parent", title: "Minecraft survival, episode 12", reason: "Gaming is on your check-with-me list." },
  { status: "block", title: "Scary story time: the haunted doll", reason: "Your rule says nothing scary." },
];

// Shared frame for sign-in and registration: the pitch on one side, the form on the other.
export default function AuthLayout({ children }) {
  return (
    <div className="auth">
      <section className="auth-intro">
        <Crown size={64} title="KidsCrown" />
        <h1>Set the rules once. KidsCrown checks every video.</h1>
        <p>
          Write what your child can and can't watch in plain language. KidsCrown checks each video against your rules
          before it's watched, and asks you only when it isn't sure.
        </p>
      </section>

      <div className="auth-form">{children}</div>

      <section className="auth-examples" aria-labelledby="examples-title">
        <h2 id="examples-title">Every video gets one of three answers</h2>
        <ul>
          {EXAMPLES.map((example) => (
            <li key={example.status}>
              <StatusBadge status={example.status} />
              <div>
                <p className="example-title">{example.title}</p>
                <p className="example-reason">{example.reason}</p>
              </div>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}
