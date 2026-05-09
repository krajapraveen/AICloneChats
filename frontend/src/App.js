import { useEffect, useState } from "react";
import { BrowserRouter, Routes, Route } from "react-router-dom";
import { Toaster } from "sonner";
import { GoogleOAuthProvider } from "@react-oauth/google";
import "./App.css";

import { AuthProvider } from "./contexts/AuthContext";
import api from "./lib/api";

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

function App() {
  // Pull Google client ID from backend config so it's never hardcoded in the bundle.
  // REMINDER: DO NOT HARDCODE THE URL, OR ADD ANY FALLBACKS OR REDIRECT URLS, THIS BREAKS THE AUTH
  const [googleClientId, setGoogleClientId] = useState(null);

  useEffect(() => {
    api.get("/auth/google/config")
      .then((r) => setGoogleClientId(r.data?.client_id || ""))
      .catch(() => setGoogleClientId(""));
  }, []);

  const inner = (
    <div className="App">
      <BrowserRouter>
        <AuthProvider>
          <AppRouter />
          <Toaster position="top-center" richColors closeButton />
        </AuthProvider>
      </BrowserRouter>
    </div>
  );

  // Wait for config so GoogleOAuthProvider gets a stable clientId on first paint.
  // Render plain shell while loading; Google button itself handles "not configured" gracefully.
  if (googleClientId === null) return inner;
  if (!googleClientId) return inner;

  return <GoogleOAuthProvider clientId={googleClientId}>{inner}</GoogleOAuthProvider>;
}

export default App;
