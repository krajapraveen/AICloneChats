import { BrowserRouter, Routes, Route, useLocation } from "react-router-dom";
import { Toaster } from "sonner";
import "./App.css";

import { AuthProvider } from "./contexts/AuthContext";
import AuthCallback from "./components/AuthCallback";

import Landing from "./pages/Landing";
import Login from "./pages/Login";
import Register from "./pages/Register";
import Dashboard from "./pages/Dashboard";
import CloneEditor from "./pages/CloneEditor";
import MemoryManager from "./pages/MemoryManager";
import PublicClone from "./pages/PublicClone";

function AppRouter() {
  const location = useLocation();
  // CRITICAL: Detect session_id during render (NOT in useEffect)
  if (location.hash?.includes("session_id=")) {
    return <AuthCallback />;
  }

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/register" element={<Register />} />
      <Route path="/auth/callback" element={<AuthCallback />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/clones/new" element={<CloneEditor />} />
      <Route path="/clones/:cloneId/edit" element={<CloneEditor />} />
      <Route path="/clones/:cloneId/memories" element={<MemoryManager />} />
      <Route path="/:slug" element={<PublicClone />} />
    </Routes>
  );
}

function App() {
  return (
    <div className="App">
      <BrowserRouter>
        <AuthProvider>
          <AppRouter />
          <Toaster position="top-center" richColors closeButton />
        </AuthProvider>
      </BrowserRouter>
    </div>
  );
}

export default App;
