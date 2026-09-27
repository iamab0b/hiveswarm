import React from "react";
import ReactDOM from "react-dom/client";
import { createBrowserRouter, RouterProvider } from "react-router-dom";
import "./index.css";
import { Shell } from "./components/Shell";
import Swarm from "./pages/Swarm";
import InboxPage from "./pages/Inbox";
import LeadPage from "./pages/Lead";
import SessionPage from "./pages/Session";
import TaskPage from "./pages/Task";
import { AgentsPage, HivePage, StatsPage, TaskRedirect, TasksPage } from "./pages/Lists";

const router = createBrowserRouter([
  {
    path: "/",
    element: <Shell />,
    children: [
      { index: true, element: <Swarm /> },
      { path: "lead", element: <LeadPage /> },
      { path: "lead/:project", element: <LeadPage /> },
      { path: "inbox", element: <InboxPage /> },
      { path: "hive", element: <HivePage /> },
      { path: "tasks", element: <TasksPage /> },
      { path: "tasks/:id", element: <TaskPage /> },
      { path: "sessions/:id", element: <SessionPage /> },
      { path: "t/:id", element: <TaskRedirect /> },
      { path: "stats", element: <StatsPage /> },
      { path: "agents", element: <AgentsPage /> },
    ],
  },
]);

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <RouterProvider router={router} />
  </React.StrictMode>,
);
