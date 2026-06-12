import { BrowserRouter, Routes, Route, Navigate } from "react-router-dom";
import { Toaster } from "sonner";
import { GoogleOAuthProvider } from "@react-oauth/google";
import "./App.css";

import { AuthProvider } from "./contexts/AuthContext";
import { GoogleAuthConfigProvider, useGoogleAuthConfig } from "./contexts/GoogleAuthConfigContext";

import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import ForgotPassword from "./pages/ForgotPassword";
import ResetPassword from "./pages/ResetPassword";
import Terms from "./pages/Terms";
import Privacy from "./pages/Privacy";
import AcceptableUse from "./pages/AcceptableUse";
import CookiePolicy from "./pages/CookiePolicy";
import Security from "./pages/Security";
import PrivacySettings from "./pages/PrivacySettings";
import Account from "./pages/Account";
import MySpace from "./pages/account/MySpace";
import ChangePassword from "./pages/account/ChangePassword";
import Subscriptions from "./pages/account/Subscriptions";
import DeleteAccount from "./pages/account/DeleteAccount";
import Inbox from "./pages/account/Inbox";
import AdminSupport from "./pages/AdminSupport";
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
import VideoAvatarChat from "./pages/VideoAvatarChat";
import AvatarProfiles from "./pages/AvatarProfiles";
import AdminAvatarChat from "./pages/AdminAvatarChat";
import DelayedChat from "./pages/DelayedChat";
import AdminDelayedMessages from "./pages/AdminDelayedMessages";
import DelayedMessageReveal from "./pages/DelayedMessageReveal";
import ConversationMemory from "./pages/ConversationMemory";
import AdminIndex from "./pages/AdminIndex";
import AdminWebhookLogs from "./pages/AdminWebhookLogs";
import AdminRevenue from "./pages/AdminRevenue";
import AdminEmailHealth from "./pages/AdminEmailHealth";
import Pricing from "./pages/Pricing";
import VerifyEmail from "./pages/VerifyEmail";
import PaymentReturn from "./pages/PaymentReturn";
import BackButton from "./components/BackButton";
import GlobalPaywallModal from "./components/GlobalPaywallModal";

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
      <Route path="/signup" element={<Register />} />
      <Route path="/forgot-password" element={<ForgotPassword />} />
      <Route path="/reset-password" element={<ResetPassword />} />
      <Route path="/terms" element={<Terms />} />
      <Route path="/terms-of-service" element={<Terms />} />
      <Route path="/privacy" element={<Privacy />} />
      <Route path="/privacy-policy" element={<Privacy />} />
      <Route path="/cookie-policy" element={<CookiePolicy />} />
      <Route path="/security" element={<Security />} />
      <Route path="/privacy-settings" element={<PrivacySettings />} />
      <Route path="/acceptable-use" element={<AcceptableUse />} />
      <Route path="/account" element={<Account />}>
        <Route index element={<MySpace />} />
        <Route path="space" element={<MySpace />} />
        <Route path="inbox" element={<Inbox />} />
        <Route path="concerns" element={<Inbox />} />
        <Route path="settings/change-password" element={<ChangePassword />} />
        <Route path="settings/subscriptions" element={<Subscriptions />} />
        <Route path="settings/delete-account" element={<DeleteAccount />} />
      </Route>
      <Route path="/admin/support" element={<AdminSupport />} />
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
      <Route path="/admin" element={<AdminIndex />} />
      <Route path="/admin/webhook-logs" element={<AdminWebhookLogs />} />
      <Route path="/admin/revenue" element={<AdminRevenue />} />
      <Route path="/admin/email-health" element={<AdminEmailHealth />} />
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
      <Route path="/video-avatar-chat" element={<VideoAvatarChat />} />
      <Route path="/video-avatar-chat/profiles" element={<AvatarProfiles />} />
      <Route path="/admin/avatar-chat" element={<AdminAvatarChat />} />
      <Route path="/delayed-chat" element={<DelayedChat />} />
      <Route path="/scheduled-messages" element={<DelayedChat />} />
      <Route path="/admin/delayed-messages" element={<AdminDelayedMessages />} />
      <Route path="/conversation-memory" element={<ConversationMemory />} />
      <Route path="/open/:token" element={<DelayedMessageReveal />} />
      <Route path="/pricing" element={<Pricing />} />
      <Route path="/verify-email" element={<VerifyEmail />} />
      <Route path="/pay/return" element={<PaymentReturn />} />
      <Route path="/auth/callback" element={<LegacyAuthRedirect />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/clones/new" element={<CloneEditor />} />
      <Route path="/create" element={<CloneEditor />} />
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
        <BackButton />
        <AppRouter />
        <GlobalPaywallModal />
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
