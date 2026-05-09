import { useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { toast } from "sonner";
import api from "../lib/api";
import { useAuth } from "../contexts/AuthContext";
import Navbar from "../components/Navbar";
import PersonalitySlider from "../components/PersonalitySlider";

const DEFAULT_PERSONALITY = {
  tone: "direct",
  humor_level: 5,
  directness: 6,
  warmth: 6,
  energy: 6,
  reply_length: "short",
  emoji_usage: "low",
  catchphrases: [],
  common_words: [],
  avoid_words: [],
};

export default function CloneEditor() {
  const { cloneId } = useParams();
  const isEdit = Boolean(cloneId);
  const { user, loading: authLoading } = useAuth();
  const navigate = useNavigate();
  const [loading, setLoading] = useState(false);
  const [saving, setSaving] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef(null);

  const [form, setForm] = useState({
    slug: "",
    display_name: "",
    bio: "",
    avatar_url: "",
    visibility: "public",
    allowed_topics: "",
    blocked_topics: "",
    personality: { ...DEFAULT_PERSONALITY },
    catchphrases_text: "",
    avoid_words_text: "",
  });

  useEffect(() => {
    if (!authLoading && !user) navigate("/login");
  }, [authLoading, user, navigate]);

  useEffect(() => {
    if (!isEdit || !user) return;
    (async () => {
      setLoading(true);
      try {
        const { data } = await api.get(`/clones/${cloneId}`);
        setForm({
          slug: data.slug,
          display_name: data.display_name,
          bio: data.bio || "",
          avatar_url: data.avatar_url || "",
          visibility: data.visibility || "public",
          allowed_topics: (data.allowed_topics || []).join(", "),
          blocked_topics: (data.blocked_topics || []).join(", "),
          personality: { ...DEFAULT_PERSONALITY, ...(data.personality || {}) },
          catchphrases_text: ((data.personality || {}).catchphrases || []).join(", "),
          avoid_words_text: ((data.personality || {}).avoid_words || []).join(", "),
        });
      } catch (e) {
        toast.error("Couldn't load clone");
        navigate("/dashboard");
      } finally {
        setLoading(false);
      }
    })();
  }, [isEdit, cloneId, user, navigate]);

  const setPersonality = (key, value) => {
    setForm((f) => ({ ...f, personality: { ...f.personality, [key]: value } }));
  };

  const handleAvatar = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) { toast.error("Max 5MB"); return; }
    setUploading(true);
    try {
      const fd = new FormData();
      fd.append("file", file);
      const { data } = await api.post("/storage/upload-avatar", fd, {
        headers: { "Content-Type": "multipart/form-data" },
      });
      setForm((f) => ({ ...f, avatar_url: data.avatar_url }));
      toast.success("Avatar uploaded");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Upload failed");
    } finally {
      setUploading(false);
    }
  };

  const submit = async (e) => {
    e.preventDefault();
    setSaving(true);
    const payload = {
      slug: form.slug.toLowerCase().trim(),
      display_name: form.display_name.trim(),
      bio: form.bio.trim(),
      avatar_url: form.avatar_url,
      visibility: form.visibility,
      allowed_topics: form.allowed_topics.split(",").map((s) => s.trim()).filter(Boolean),
      blocked_topics: form.blocked_topics.split(",").map((s) => s.trim()).filter(Boolean),
      personality: {
        ...form.personality,
        catchphrases: form.catchphrases_text.split(",").map((s) => s.trim()).filter(Boolean),
        avoid_words: form.avoid_words_text.split(",").map((s) => s.trim()).filter(Boolean),
      },
    };

    try {
      if (isEdit) {
        // PATCH (no slug required)
        const { slug, ...rest } = payload;
        await api.patch(`/clones/${cloneId}`, rest);
        toast.success("Clone updated");
      } else {
        await api.post("/clones", payload);
        toast.success("Clone created!");
      }
      navigate("/dashboard");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Save failed");
    } finally {
      setSaving(false);
    }
  };

  const onDelete = async () => {
    if (!isEdit) return;
    if (!window.confirm("Delete this clone forever? This can't be undone.")) return;
    try {
      await api.delete(`/clones/${cloneId}`);
      toast.success("Clone deleted");
      navigate("/dashboard");
    } catch (err) {
      toast.error(err?.response?.data?.detail || "Delete failed");
    }
  };

  if (authLoading || !user || loading) {
    return <div className="min-h-screen bg-cream flex items-center justify-center font-display">Loading…</div>;
  }

  return (
    <div className="min-h-screen bg-cream">
      <Navbar />
      <form onSubmit={submit} className="max-w-3xl mx-auto px-5 md:px-8 py-10" data-testid="clone-editor-form">
        <p className="font-mono text-xs uppercase tracking-widest text-muted-foreground mb-2">{isEdit ? "EDITING" : "NEW CLONE"}</p>
        <h1 className="heading-display text-4xl md:text-5xl mb-8">{isEdit ? "Tune your clone." : "Build your clone."}</h1>

        {/* Identity */}
        <div className="brutal-card p-6 md:p-8 mb-6">
          <h2 className="heading-display text-2xl mb-5">1. Identity</h2>

          <div className="flex items-center gap-5 mb-5">
            {form.avatar_url ? (
              <img src={form.avatar_url.startsWith("/") ? `${process.env.REACT_APP_BACKEND_URL}${form.avatar_url}` : form.avatar_url} alt="avatar" className="w-20 h-20 rounded-full border-2 border-ink object-cover" />
            ) : (
              <div className="w-20 h-20 rounded-full border-2 border-ink bg-lilac flex items-center justify-center font-display font-black text-2xl">
                {form.display_name?.[0]?.toUpperCase() || "?"}
              </div>
            )}
            <div>
              <input ref={fileRef} type="file" accept="image/png,image/jpeg,image/webp,image/gif" onChange={handleAvatar} className="hidden" data-testid="avatar-file-input" />
              <button type="button" onClick={() => fileRef.current?.click()} className="btn-ghost text-sm" disabled={uploading} data-testid="avatar-upload-btn">
                {uploading ? "Uploading…" : form.avatar_url ? "Change avatar" : "Upload avatar"}
              </button>
              <p className="text-xs text-muted-foreground mt-2">PNG / JPG / WebP, max 5MB</p>
            </div>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="label-brutal block mb-1.5">Public slug</label>
              <input className="input-brutal" required disabled={isEdit} value={form.slug} onChange={(e) => setForm({ ...form, slug: e.target.value.toLowerCase().replace(/[^a-z0-9-]/g, "") })} placeholder="raja-ai" data-testid="clone-slug-input" />
              <p className="text-xs text-muted-foreground mt-1">cloneme.ai/<span className="font-bold">{form.slug || "your-slug"}</span></p>
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Display name</label>
              <input className="input-brutal" required value={form.display_name} onChange={(e) => setForm({ ...form, display_name: e.target.value })} placeholder="Raja AI" data-testid="clone-name-input" />
            </div>
            <div className="md:col-span-2">
              <label className="label-brutal block mb-1.5">Bio</label>
              <textarea className="input-brutal min-h-[90px]" maxLength={400} value={form.bio} onChange={(e) => setForm({ ...form, bio: e.target.value })} placeholder="Founder building AI creative tools. Likes blunt feedback and slow mornings." data-testid="clone-bio-input" />
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Visibility</label>
              <select className="input-brutal" value={form.visibility} onChange={(e) => setForm({ ...form, visibility: e.target.value })} data-testid="clone-visibility-select">
                <option value="public">Public</option>
                <option value="unlisted">Unlisted (link only)</option>
                <option value="private">Private (only me)</option>
              </select>
            </div>
          </div>
        </div>

        {/* Personality */}
        <div className="brutal-card p-6 md:p-8 mb-6">
          <h2 className="heading-display text-2xl mb-1">2. Personality</h2>
          <p className="text-sm text-muted-foreground mb-6 font-medium">Slide it. Make your clone sound like you, not Cortana.</p>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-x-8 gap-y-6">
            <PersonalitySlider testId="slider-humor" label="Humor" leftLabel="Serious" rightLabel="Funny" value={form.personality.humor_level} onChange={(v) => setPersonality("humor_level", v)} />
            <PersonalitySlider testId="slider-directness" label="Directness" leftLabel="Diplomatic" rightLabel="Blunt" value={form.personality.directness} onChange={(v) => setPersonality("directness", v)} />
            <PersonalitySlider testId="slider-warmth" label="Warmth" leftLabel="Cold" rightLabel="Friendly" value={form.personality.warmth} onChange={(v) => setPersonality("warmth", v)} />
            <PersonalitySlider testId="slider-energy" label="Energy" leftLabel="Chill" rightLabel="Hyped" value={form.personality.energy} onChange={(v) => setPersonality("energy", v)} />
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-7">
            <div>
              <label className="label-brutal block mb-1.5">Reply length</label>
              <select className="input-brutal" value={form.personality.reply_length} onChange={(e) => setPersonality("reply_length", e.target.value)} data-testid="reply-length-select">
                <option value="short">Short (under 60 words)</option>
                <option value="medium">Medium</option>
                <option value="detailed">Detailed</option>
              </select>
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Emoji usage</label>
              <select className="input-brutal" value={form.personality.emoji_usage} onChange={(e) => setPersonality("emoji_usage", e.target.value)} data-testid="emoji-usage-select">
                <option value="none">None</option>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </select>
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Tone</label>
              <input className="input-brutal" value={form.personality.tone} onChange={(e) => setPersonality("tone", e.target.value)} placeholder="direct, warm, witty…" data-testid="tone-input" />
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Catchphrases (comma separated)</label>
              <input className="input-brutal" value={form.catchphrases_text} onChange={(e) => setForm({ ...form, catchphrases_text: e.target.value })} placeholder="No fluff, brutal truth" data-testid="catchphrases-input" />
            </div>
            <div className="md:col-span-2">
              <label className="label-brutal block mb-1.5">Words to avoid</label>
              <input className="input-brutal" value={form.avoid_words_text} onChange={(e) => setForm({ ...form, avoid_words_text: e.target.value })} placeholder="maybe, kind of, sort of" data-testid="avoid-words-input" />
            </div>
          </div>
        </div>

        {/* Topics */}
        <div className="brutal-card p-6 md:p-8 mb-6">
          <h2 className="heading-display text-2xl mb-1">3. Topics & rules</h2>
          <p className="text-sm text-muted-foreground mb-5 font-medium">Optional. Define what your clone will and won't talk about.</p>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="label-brutal block mb-1.5">Allowed topics (comma separated)</label>
              <input className="input-brutal" value={form.allowed_topics} onChange={(e) => setForm({ ...form, allowed_topics: e.target.value })} placeholder="startups, AI, music" data-testid="allowed-topics-input" />
            </div>
            <div>
              <label className="label-brutal block mb-1.5">Blocked topics</label>
              <input className="input-brutal" value={form.blocked_topics} onChange={(e) => setForm({ ...form, blocked_topics: e.target.value })} placeholder="private finances, family" data-testid="blocked-topics-input" />
            </div>
          </div>
        </div>

        <div className="flex flex-wrap items-center gap-3">
          <button type="submit" disabled={saving} className="btn-brutal" data-testid="save-clone-btn">
            {saving ? "Saving…" : isEdit ? "Save changes" : "Create clone"}
          </button>
          {isEdit && (
            <>
              <button type="button" onClick={() => navigate(`/clones/${cloneId}/memories`)} className="btn-ghost" data-testid="manage-memories-btn">
                Manage memories
              </button>
              <button type="button" onClick={onDelete} className="btn-danger ml-auto" data-testid="delete-clone-btn">
                Delete clone
              </button>
            </>
          )}
        </div>
      </form>
    </div>
  );
}
