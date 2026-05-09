import React from "react";
import ReactDOM from "react-dom/client";
import "@/index.css";
import { enforceCanonicalHost } from "@/lib/canonicalHost";
import App from "@/App";

// Run BEFORE React mounts so OAuth flow always sees the canonical origin.
enforceCanonicalHost();

const root = ReactDOM.createRoot(document.getElementById("root"));
root.render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
