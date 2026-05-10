/**
 * Avatar Profile management — admin/QA only.
 * Create/edit/delete avatar profiles (image URL + voice + animation style + per-clone default).
 */
import { useCallback, useEffect, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import Navbar from "../components/Navbar";
import { useAuth } from "../contexts/AuthContext";

const VOICES = ["alloy", "ash", "coral", "echo", "fable", "nova", "onyx", "sage", "shimmer"];
const STYLES = ["natural", "cinematic", "expressive"];

export default function AvatarProfiles() {
  const { user, loading } = useAuth();
  const navigate = useNavigate();
  const [profiles, setProfiles] = useState([]);
  const [creating, setCreating] = useState(false);
  const [form, setForm] = useState({ avatar_name: "", avatar_image_url: "", default_voice_id: "alloy", animation_style: "natural", clone_id: "", is_default: false });

  const refresh = useCallback(async () => {
    try {
      const r = await api.get("/avatar-chat/profiles");
      setProfiles(r.data?.profiles || []);
    } catch (e) {
      if (e?.response?.status === 503) {
        toast.error("Avatar Chat disabled for public users.");
      }
    }
  }, []);

  useEffect(() => {
    if (!loading && !user) { navigate("/login?redirect=/video-avatar-chat/profiles"); return; }
    if (user) refresh();
  }, [user, loading, navigate, refresh]);

  const submit = async () => {
    if (!form.avatar_name.trim() || !form.avatar_image_url.trim()) { toast.error("Name and image URL required"); return; }
    setCreating(true);
    try {
      await api.post("/avatar-chat/profiles", {
        ...form,
        clone_id: form.clone_id.trim() || null,
      });
      toast.success("Avatar created");
      setForm({ avatar_name: "", avatar_image_url: "", default_voice_id: "alloy", animation_style: "natural", clone_id: "", is_default: false });
      await refresh();
    } catch (e) {
      toast.error(e?.response?.data?.detail || "Could not create");
    } finally {
      setCreating(false);
    }
  };

  const setDefault = async (id) => {
    try { await api.post(`/avatar-chat/profiles/${id}/default`); await refresh(); }
    catch (e) { toast.error(e?.response?.data?.detail || "Failed"); }
  };
  const remove = async (id) => {
    if (!window.confirm("Delete this avatar profile?")) return;
    try { await api.delete(`/avatar-chat/profiles/${id}`); await refresh(); }
    catch (e) { toast.error(e?.response?.data?.detail || "Failed"); }
  };

  if (loading || !user) return <div className="page-bg min-h-screen flex items-center justify-center"><div className="text-muted font-mono text-sm">loading…</div></div>;

  return (
    <div className="page-bg min-h-screen min-h-[100dvh]" data-testid="avatar-profiles-page">
      <Navbar />
      <div className="max-w-4xl mx-auto px-4 sm:px-5 md:px-8 py-6 sm:py-10">
        <Link to="/video-avatar-chat" className="text-xs font-mono text-muted hover:text-ink mb-2 inline-block">← Avatar chat</Link>
        <h1 className="heading-display text-2xl sm:text-3xl mt-1 mb-1">Avatar profiles</h1>
        <p className="text-xs text-muted mb-6">Image + voice + animation style. Default is used unless an avatar is explicitly picked when sending.</p>

        <div className="brutal-card p-5 mb-6" data-testid="avatar-profile-form">
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-3">New avatar</div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
            <input value={form.avatar_name} onChange={(e) => setForm({ ...form, avatar_name: e.target.value })} placeholder="Avatar name" className="input-brutal text-sm" data-testid="avatar-form-name" maxLength={60} />
            <input value={form.clone_id} onChange={(e) => setForm({ ...form, clone_id: e.target.value })} placeholder="Clone ID (optional, scope to clone)" className="input-brutal text-sm" data-testid="avatar-form-clone" />
            <input value={form.avatar_image_url} onChange={(e) => setForm({ ...form, avatar_image_url: e.target.value })} placeholder="Image URL (publicly fetchable)" className="input-brutal text-sm md:col-span-2" data-testid="avatar-form-image" />
            <select value={form.default_voice_id} onChange={(e) => setForm({ ...form, default_voice_id: e.target.value })} className="input-brutal text-sm" data-testid="avatar-form-voice">
              {VOICES.map((v) => <option key={v} value={v}>{v}</option>)}
            </select>
            <select value={form.animation_style} onChange={(e) => setForm({ ...form, animation_style: e.target.value })} className="input-brutal text-sm" data-testid="avatar-form-style">
              {STYLES.map((s) => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <label className="flex items-center gap-2 mt-3 text-xs font-mono">
            <input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} data-testid="avatar-form-default" />
            Make this my default
          </label>
          <button onClick={submit} disabled={creating} className="btn-brutal mt-4 disabled:opacity-50" data-testid="avatar-form-submit">{creating ? "Creating…" : "Create avatar"}</button>
        </div>

        <div className="grid grid-cols-1 md:grid-cols-2 gap-3" data-testid="avatar-profile-list">
          {profiles.length === 0 && <div className="text-muted text-sm">No avatars yet.</div>}
          {profiles.map((p) => (
            <div key={p.avatar_id} className="brutal-card p-4 flex items-center gap-3" data-testid={`avatar-profile-${p.avatar_id}`}>
              <img src={p.avatar_image_url} alt={p.avatar_name} className="w-14 h-14 rounded-md object-cover border border-ink/15" onError={(e) => { e.target.style.display = "none"; }} />
              <div className="flex-1 min-w-0">
                <div className="text-sm font-bold truncate">{p.avatar_name} {p.is_default && <span className="text-amber text-xs">★ default</span>}</div>
                <div className="text-[10px] font-mono text-muted">{p.default_voice_id} · {p.animation_style}{p.clone_id ? ` · clone:${p.clone_id.slice(-6)}` : ""}</div>
              </div>
              {!p.is_default && <button onClick={() => setDefault(p.avatar_id)} className="btn-ghost text-xs" data-testid={`avatar-default-${p.avatar_id}`}>Set default</button>}
              <button onClick={() => remove(p.avatar_id)} className="btn-ghost text-xs text-red-300" data-testid={`avatar-delete-${p.avatar_id}`}>Delete</button>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
