import { Link, useNavigate } from "react-router-dom";
import { useFamily } from "../family.jsx";
import AddChildForm from "../components/AddChildForm.jsx";
import { MAX_KIDS } from "../constants.js";

export default function AddChild() {
  const { profiles, setView } = useFamily();
  const navigate = useNavigate();

  if (profiles.length >= MAX_KIDS) {
    return (
      <>
        <h1>Add a child</h1>
        <div className="panel">
          <p>You already have {MAX_KIDS} children, the maximum. Remove one from your profile to add another.</p>
          <Link className="btn btn-secondary" to="/profile">
            Go to profile
          </Link>
        </div>
      </>
    );
  }

  return (
    <>
      <h1>Add a child</h1>
      <section className="card">
        <AddChildForm
          submitLabel="Add child"
          onCancel={() => navigate("/")}
          onAdded={(created) => {
            setView(created.id);
            navigate("/");
          }}
        />
      </section>
    </>
  );
}
