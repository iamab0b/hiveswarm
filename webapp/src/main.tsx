import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, Navigate, RouterProvider, useParams } from "react-router-dom";
import "./index.css";
import { Shell } from "./components/Shell";
import TerminalsPage from "./pages/Terminals";
import ControlPage from "./pages/Control";
import { useStore } from "./lib/store";
import { HOME } from "./lib/utils";

/** Old addresses of a task or a session: open its record over the workspace it belongs to. */
function TaskRedirect() {
  const { id = "" } = useParams();
  const t = useStore((s) => s.taskById[id]);
  const boot = useStore((s) => s.bootstrapped);
  const openDetail = useStore((s) => s.openDetail);
  React.useEffect(() => { if (t) openDetail(t.id); }, [t, openDetail]);
  if (!boot) return <div className="p-6 text-[12px] text-dim">loading</div>;
  if (!t) return <Navigate to="/control" replace />;
  return <Navigate to={t.project === HOME ? "/" : `/p/${t.project}`} replace />;
}

function LeadRedirect() {
  const { project } = useParams();
  return <Navigate to={project ? `/p/${project}` : "/control"} replace />;
}

// Home and a project's workspace are the same page element on purpose: when the Home chat starts a project and the
// address changes, the page (and the chat's terminal in it) stays mounted.
const terminals = <TerminalsPage />;

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: terminals },
      { path: "p/:project", element: terminals },
      { path: "control", element: <ControlPage /> },
      { path: "lead", element: <LeadRedirect /> },
      { path: "lead/:project", element: <LeadRedirect /> },
      { path: "tasks/:id", element: <TaskRedirect /> },
      { path: "sessions/:id", element: <TaskRedirect /> },
      { path: "t/:id", element: <TaskRedirect /> },
      { path: "*", element: <Navigate to="/control" replace /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
