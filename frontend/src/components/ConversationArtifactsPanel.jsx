/**
 * Conversation Artifacts Panel.
 *
 * Pull-not-push. The panel only does work when the user clicks "Extract".
 * No background polling. No auto-extraction. No reminders. No nudges.
 *
 * Renders inside an existing clone chat surface (PublicClone, future authed chats).
 * Identity:
 *  - If the user is authenticated, server uses the auth header.
 *  - Else `visitor_id` (already used by clone chat) is the identity key.
 */
import { useCallback, useEffect, useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

function StatusDot({ status }) {
  const map = {
    open: "bg-amber",
    in_progress: "bg-violet",
    done: "bg-emerald",
    cancelled: "bg-ink/30",
  };
  return <span className={`inline-block w-2 h-2 rounded-full ${map[status] || "bg-ink/40"}`} />;
}

function PriorityTag({ p }) {
  const map = { low: "border-ink/20 text-muted", medium: "border-amber/40 text-amber", high: "border-red-400/40 text-red-300" };
  return <span className={`text-[9px] font-mono uppercase tracking-widest border px-1.5 py-0.5 rounded-full ${map[p] || ""}`}>{p}</span>;
}

function fmtDue(iso) {
  if (!iso) return null;
  try {
    const d = new Date(iso);
    return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
  } catch { return null; }
}

function TaskRow({ task, onUpdate, onDelete }) {
  const cycle = () => {
    const next = task.status === "open" ? "in_progress" : task.status === "in_progress" ? "done" : "open";
    onUpdate(task.task_id, { status: next });
  };
  return (
    <div className="flex items-start gap-2 py-2 border-b border-white/5 last:border-0" data-testid={`artifact-task-${task.task_id}`}>
      <button onClick={cycle} className="mt-1.5" title={task.status} data-testid={`artifact-task-cycle-${task.task_id}`}>
        <StatusDot status={task.status} />
      </button>
      <div className="flex-1 min-w-0">
        <div className={`text-sm ${task.status === "done" ? "line-through opacity-60" : ""}`}>{task.title}</div>
        {task.description && <div className="text-[11px] text-muted mt-0.5 line-clamp-2">{task.description}</div>}
        <div className="flex items-center gap-2 mt-1">
          <PriorityTag p={task.priority} />
          {task.due_at && <span className="text-[10px] font-mono text-muted">due {fmtDue(task.due_at)}</span>}
        </div>
      </div>
      <button onClick={() => onDelete(task.task_id)} className="text-[10px] font-mono text-muted hover:text-red-300" data-testid={`artifact-task-del-${task.task_id}`}>×</button>
    </div>
  );
}

export default function ConversationArtifactsPanel({ conversationId, visitorId }) {
  const [artifacts, setArtifacts] = useState([]);
  const [tasks, setTasks] = useState([]);
  const [open, setOpen] = useState(false);
  const [extracting, setExtracting] = useState(false);

  const refresh = useCallback(async () => {
    if (!conversationId) return;
    try {
      const params = visitorId ? `?conversation_id=${conversationId}&visitor_id=${encodeURIComponent(visitorId)}` : `?conversation_id=${conversationId}`;
      const [a, t] = await Promise.all([
        api.get(`/clone-artifacts${params}`),
        api.get(`/clone-artifacts/tasks${params}`),
      ]);
      setArtifacts(a.data?.artifacts || []);
      setTasks(t.data?.tasks || []);
    } catch (e) {
      // 404 = no conversation yet (first message hasn't been sent). Quiet noop.
    }
  }, [conversationId, visitorId]);

  useEffect(() => {
    if (open && conversationId) refresh();
  }, [open, conversationId, refresh]);

  const onExtract = async () => {
    if (!conversationId || extracting) return;
    setExtracting(true);
    try {
      await api.post("/clone-artifacts/extract", { conversation_id: conversationId, visitor_id: visitorId });
      toast.success("Artifacts extracted");
      await refresh();
    } catch (e) {
      const code = e?.response?.status;
      const msg = e?.response?.data?.detail;
      if (code === 400 && (msg || "").toLowerCase().includes("no conversation")) toast.message("Send a message first.");
      else toast.error(msg || "Extraction failed");
    } finally {
      setExtracting(false);
    }
  };

  const onUpdate = async (taskId, payload) => {
    try {
      const params = visitorId ? `?visitor_id=${encodeURIComponent(visitorId)}` : "";
      await api.patch(`/clone-artifacts/tasks/${taskId}${params}`, payload);
      await refresh();
    } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); }
  };

  const onDelete = async (taskId) => {
    try {
      const params = visitorId ? `?visitor_id=${encodeURIComponent(visitorId)}` : "";
      await api.delete(`/clone-artifacts/tasks/${taskId}${params}`);
      await refresh();
    } catch (e) { toast.error(e?.response?.data?.detail || "Failed"); }
  };

  if (!conversationId) return null;

  const latest = artifacts[0];

  return (
    <div className="mt-4" data-testid="artifacts-panel">
      <button onClick={() => setOpen(!open)} className="w-full glass-card p-3 flex items-center justify-between text-left hover:bg-white/5 transition" data-testid="artifacts-panel-toggle">
        <div>
          <div className="text-[11px] font-mono uppercase tracking-widest text-muted">What mattered in this conversation</div>
          <div className="text-sm text-ink/80 mt-0.5">
            {latest ? `${tasks.length} item${tasks.length === 1 ? "" : "s"} extracted · ${artifacts.length} extraction${artifacts.length === 1 ? "" : "s"}` : "Pull-based memory. Open to extract."}
          </div>
        </div>
        <span className="text-xs font-mono text-muted">{open ? "−" : "+"}</span>
      </button>

      {open && (
        <div className="glass-card p-4 mt-2" data-testid="artifacts-panel-body">
          <button onClick={onExtract} disabled={extracting} className="btn-brutal w-full disabled:opacity-50 mb-3" data-testid="artifacts-extract-btn">
            {extracting ? "Reading the conversation…" : latest ? "Re-extract from latest messages" : "Extract artifacts"}
          </button>

          {latest?.summary && (
            <div className="mb-4" data-testid="artifacts-summary">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-1">Summary</div>
              <div className="text-sm text-ink/85 whitespace-pre-wrap">{latest.summary}</div>
            </div>
          )}

          {tasks.length > 0 && (
            <div className="mb-4" data-testid="artifacts-tasks">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-1">Tasks ({tasks.filter((t) => t.status !== "done").length} open)</div>
              <div>
                {tasks.map((t) => <TaskRow key={t.task_id} task={t} onUpdate={onUpdate} onDelete={onDelete} />)}
              </div>
            </div>
          )}

          {(latest?.decisions || []).length > 0 && (
            <div className="mb-4" data-testid="artifacts-decisions">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-1">Decisions</div>
              {latest.decisions.map((d, i) => (
                <div key={i} className="py-2 border-b border-white/5 last:border-0">
                  <div className="text-sm">{d.title}</div>
                  {d.reason && <div className="text-[11px] text-muted mt-0.5">{d.reason}</div>}
                </div>
              ))}
            </div>
          )}

          {(latest?.follow_ups || []).length > 0 && (
            <div className="mb-4" data-testid="artifacts-followups">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-1">Follow-ups (no schedule)</div>
              {latest.follow_ups.map((f, i) => (
                <div key={i} className="py-2 border-b border-white/5 last:border-0">
                  <div className="text-sm">{f.title}</div>
                  {f.context && <div className="text-[11px] text-muted mt-0.5">{f.context}</div>}
                </div>
              ))}
            </div>
          )}

          {(latest?.unresolved_questions || []).length > 0 && (
            <div className="mb-2" data-testid="artifacts-questions">
              <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-1">Unresolved</div>
              {latest.unresolved_questions.map((q, i) => (<div key={i} className="text-sm text-ink/80 py-1">· {q}</div>))}
            </div>
          )}

          {!latest && (
            <div className="text-xs text-muted italic">No artifacts yet. Click extract to ask the clone what mattered.</div>
          )}
        </div>
      )}
    </div>
  );
}
