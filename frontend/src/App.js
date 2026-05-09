import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import { GoogleOAuthProvider } from "@react-oauth/google";
import "./App.css";

import { AuthProvider } from "./contexts/AuthContext";
import { GoogleAuthConfigProvider, useGoogleAuthConfig } from "./contexts/GoogleAuthConfigContext";

import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import CloneEditor from "./pages/CloneEditor";
import MemoryManager from "./pages/MemoryManager";
import PublicClone from "./pages/PublicClone";
import Explore from "./pages/Explore";
import MoodChat from "./pages/MoodChat";
import SmartReplyStudio from "./pages/SmartReplyStudio";
import SmartReplyHistory from "./pages/SmartReplyHistory";
import SmartReplyFavorites from "./pages/SmartReplyFavorites";
import AdminLoginIntelligence from "./pages/AdminLoginIntelligence";

function AppRouter() {
  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/explore" element={<Explore />} />
      <Route path="/mood-chat" element={<MoodChat />} />
      <Route path="/smart-reply" element={<SmartReplyStudio />} />
      <Route path="/smart-reply/history" element={<SmartReplyHistory />} />
      <Route path="/smart-reply/favorites" element={<SmartReplyFavorites />} />
      <Route path="/admin/login-intelligence" element={<AdminLoginIntelligence />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/clones/new" element={<CloneEditor />} />
      <Route path="/clones/:cloneId/edit" element={<CloneEditor />} />
      <Route path="/clones/:cloneId/memories" element={<MemoryManager />} />
      <Route path="/:slug" element={<PublicClone />} />
    </Routes>
  );
}

/**
 * Children render only after Google config is known. This guarantees that any descendant
 * using @react-oauth/google will have a GoogleOAuthProvider ancestor in the tree.
 */
function ConfiguredApp() {
  const { configured, clientId } = useGoogleAuthConfig();

  const inner = (
    <BrowserRouter>
      <AuthProvider>
        <AppRouter />
        <Toaster position="top-center" richColors closeButton />
      </AuthProvider>
    </BrowserRouter>
  );

  if (!configured || !clientId) return inner;
  return <GoogleOAuthProvider clientId={clientId}>{inner}</GoogleOAuthProvider>;
}

const Splash = (
  <div className="App page-bg" style={{ minHeight: "100vh", display: "flex", alignItems: "center", justifyContent: "center" }}>
    <div style={{ color: "rgba(255,255,255,0.5)", fontFamily: "monospace", fontSize: 13 }}>loading…</div>
  </div>
);

function App() {
  return (
    <div className="App">
      <GoogleAuthConfigProvider fallback={Splash}>
        <ConfiguredApp />
      </GoogleAuthConfigProvider>
    </div>
  );
}

export default App;
