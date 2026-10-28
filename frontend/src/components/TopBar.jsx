import { Link } from "react-router-dom";
import Crown from "./Crown.jsx";
import ViewSwitcher from "./ViewSwitcher.jsx";
import ParentMenu from "./ParentMenu.jsx";

export default function TopBar() {
  return (
    <header className="topbar">
      <Link to="/" className="brand" aria-label="KidsCrown home">
        <Crown size={34} />
        <span className="brand-name">KidsCrown</span>
      </Link>
      <div className="topbar-controls">
        <ViewSwitcher />
        <ParentMenu />
      </div>
    </header>
  );
}
