import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
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
import VoiceMessaging from "./pages/VoiceMessaging";
import VoiceHistory from "./pages/VoiceHistory";
import VoiceSharePublic from "./pages/VoiceSharePublic";
import AnonymousReality from "./pages/AnonymousReality";
import AnonymousRoom from "./pages/AnonymousRoom";
import AnonymousAdmin from "./pages/AnonymousAdmin";
import AdminLoginIntelligence from "./pages/AdminLoginIntelligence";
import AdminVoiceMetrics from "./pages/AdminVoiceMetrics";
import AdminAnonymousMetrics from "./pages/AdminAnonymousMetrics";
import Debates from "./pages/Debates";
import DebateRoom from "./pages/DebateRoom";
import DebateResults from "./pages/DebateResults";
import DebatesAdmin from "./pages/DebatesAdmin";
import AdminDebatesRetention from "./pages/AdminDebatesRetention";
import AdminSafety from "./pages/AdminSafety";
import AdminChats from "./pages/AdminChats";
import TranslationChatPage from "./pages/TranslationChatPage";
import TranslationRoom from "./pages/TranslationRoom";
import AdminTranslationChat from "./pages/AdminTranslationChat";

function LegacyAuthRedirect() {
  // /auth/callback was the Emergent OAuth landing route. Custom Google flow no longer needs it.
  return <Navigate to="/login" replace />;
}

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
      <Route path="/voice" element={<VoiceMessaging />} />
      <Route path="/voice/history" element={<VoiceHistory />} />
      <Route path="/v/:shareId" element={<VoiceSharePublic />} />
      <Route path="/anonymous-reality" element={<AnonymousReality />} />
      <Route path="/anonymous-reality/:slug" element={<AnonymousRoom />} />
      <Route path="/admin/login-intelligence" element={<AdminLoginIntelligence />} />
      <Route path="/admin/voice-metrics" element={<AdminVoiceMetrics />} />
      <Route path="/admin/anonymous-reality" element={<AnonymousAdmin />} />
      <Route path="/admin/anonymous-metrics" element={<AdminAnonymousMetrics />} />
      <Route path="/debates" element={<Debates />} />
      <Route path="/debates/:slug" element={<DebateRoom />} />
      <Route path="/debates/:slug/results" element={<DebateResults />} />
      <Route path="/admin/debates" element={<DebatesAdmin />} />
      <Route path="/admin/debates/retention" element={<AdminDebatesRetention />} />
      <Route path="/admin/safety" element={<AdminSafety />} />
      <Route path="/admin/chats" element={<AdminChats />} />
      <Route path="/translation-chat" element={<TranslationChatPage />} />
      <Route path="/translation-chat/:roomId" element={<TranslationRoom />} />
      <Route path="/admin/translation-chat" element={<AdminTranslationChat />} />
      <Route path="/auth/callback" element={<LegacyAuthRedirect />} />
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
