import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";

export default function MemoryManager() {
  const { cloneId } = useParams();
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [clone, setClone] = useState(null);
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);

  const [form, setForm] = useState({
    content: "",
    memory_type: "factual",
    importance: 0.7,
    visibility: "public",
  });

  useEffect(() => {
    if (!authLoading && !user) navigate("/login");
  }, [authLoading, user, navigate]);

  useEffect(() => {
    if (!user) return;
    (async () => {
      try {
        const [c, m] = await Promise.all([
          api.get(`/clones/${cloneId}`),
          api.get(`/clones/${cloneId}/memories`),
        ]);
        setClone(c.data);
        setMemories(m.data);
      } catch {
        toast.error("Couldn't load memories");
        navigate("/dashboard");
      } finally {
        setLoading(false);
      }
    })();
  }, [cloneId, user, navigate]);

  const addMemory = async (e) => {
    e.preventDefault();
    if (!form.content.trim()) return;
    setAdding(true);
    try {
      const { data } = await api.post(`/clones/${cloneId}/memories`, {
        content: form.content.trim(),
        memory_type: form.memory_type,
        importance: parseFloat(form.importance),
        visibility: form.visibility,
        can_use_for_reply: form.visibility !== "owner_only",
      });
      setMemories([data, ...memories]);
      setForm({ ...form, content: "" });
      toast.success("Memory saved");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Failed");
    } finally {
      setAdding(false);
    }
  };

  const removeMemory = async (id) => {
    if (!window.confirm("Delete this memory?")) return;
    try {
      await api.delete(`/clones/${cloneId}/memories/${id}`);
      setMemories(memories.filter((m) => m.memory_id !== id));
      toast.success("Deleted");
    } catch {
      toast.error("Couldn't delete");
    }
  };

  const toggleUse = async (m) => {
    try {
      const { data } = await api.patch(`/clones/${cloneId}/memories/${m.memory_id}`, {
        can_use_for_reply: !m.can_use_for_reply,
      });
      setMemories(memories.map((x) => (x.memory_id === m.memory_id ? data : x)));
    } catch {
      toast.error("Couldn't update");
    }
  };

  if (authLoading || !user || loading) {
    return <div className="page-bg flex items-center justify-center font-display text-ink min-h-screen">Loading…</div>;
  }

  return (
    <div className="page-bg min-h-screen">
      <Navbar />
      <div className="orb orb-amber w-[380px] h-[380px] -top-16 -right-20 opacity-25 animate-orb" aria-hidden />
      <div className="max-w-4xl mx-auto px-5 md:px-8 py-10 relative" data-testid="memory-manager">
        <p className="font-mono text-xs uppercase tracking-widest text-muted mb-2">MEMORY MANAGER</p>
        <h1 className="heading-display text-4xl md:text-5xl">{clone?.display_name}'s memories</h1>
        <p className="mt-2 text-muted font-medium">
          Add facts, preferences, relationships. Your clone will use these in conversations.
        </p>

        <div className="flex items-center gap-3 mt-4 mb-8">
          <Link to={`/clones/${cloneId}/edit`} className="btn-ghost text-sm" data-testid="back-to-editor-btn">← Back to clone</Link>
          <Link to={`/${clone?.slug}`} className="btn-ghost text-sm" data-testid="view-public-btn">View public page</Link>
        </div>

        {/* Add new */}
        <form onSubmit={addMemory} className="brutal-card p-6 mb-8" data-testid="add-memory-form">
          <h2 className="heading-display text-xl mb-4">Add a memory</h2>
          <textarea
            className="input-brutal min-h-[80px] mb-4"
            required
            value={form.content}
            onChange={(e) => setForm({ ...form, content: e.target.value })}
            placeholder="e.g. I'm a fan of brutally honest feedback. Or: I grew up in Bangalore."
            data-testid="memory-content-input"
          />
          <div className="grid grid-cols-1 md:grid-cols-3 gap-3 mb-4">
            <div>
              <label className="label-brutal block mb-1.5">Type</label>
              <select className="input-brutal" value={form.memory_type} onChange={(e) => setForm({ ...form, memory_type: e.target.value })} data-testid="memory-type-select">
                <option value="factual">Factual</option>
                <option value="preference">Preference</option>
                <option value="relationship">Relationship</option>
                <option value="profile">Profile</option>
                <option value="style">Style</option>
              </select>
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Importance ({form.importance})</label>
              <input type="range" min="0" max="1" step="0.05" className="range-brutal" value={form.importance} onChange={(e) => setForm({ ...form, importance: e.target.value })} data-testid="memory-importance-input" />
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Visibility</label>
              <select className="input-brutal" value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value })} data-testid="memory-visibility-select">
                <option value="public">Public (used in chats)</option>
                <option value="private">Private (used but hidden)</option>
                <option value="owner_only">Owner only (never used)</option>
              </select>
            </div>
          </div>
          <button type="submit" disabled={adding} className="btn-brutal" data-testid="add-memory-btn">
            {adding ? "Saving…" : "+ Save memory"}
          </button>
        </form>

        {/* List */}
        {memories.length === 0 ? (
          <div className="brutal-card p-10 text-center bg-lemon" data-testid="memories-empty">
            <p className="font-display text-xl">No memories yet. Add the first one above.</p>
          </div>
        ) : (
          <div className="space-y-3" data-testid="memories-list">
            {memories.map((m) => (
              <div key={m.memory_id} className={`brutal-card p-5 ${m.can_use_for_reply ? "" : "opacity-60"}`} data-testid={`memory-row-${m.memory_id}`}>
                <div className="flex items-start gap-3">
                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-2 flex-wrap">
                      <span className="tag bg-mint">{m.memory_type}</span>
                      <span className={`tag ${m.visibility === "public" ? "bg-lilac" : m.visibility === "private" ? "bg-bubblegum" : "bg-cream"}`}>
                        {m.visibility}
                      </span>
                      <span className="tag bg-lemon">★ {(m.importance ?? 0).toFixed(2)}</span>
                    </div>
                    <p className="font-medium">{m.content}</p>
                  </div>
                  <div className="flex flex-col gap-2">
                    <button onClick={() => toggleUse(m)} className="btn-ghost text-xs py-1.5" data-testid={`toggle-${m.memory_id}`}>
                      {m.can_use_for_reply ? "Disable" : "Enable"}
                    </button>
                    <button onClick={() => removeMemory(m.memory_id)} className="btn-danger" data-testid={`delete-mem-${m.memory_id}`}>
                      Delete
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
