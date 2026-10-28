import { Outlet } from "react-router-dom";
import TopBar from "./TopBar.jsx";

export default function AppShell() {
  return (
    <>
      <a className="skip-link" href="#main">
        Skip to content
      </a>
      <TopBar />
      <main id="main" className="page">
        <Outlet />
      </main>
    </>
  );
}
