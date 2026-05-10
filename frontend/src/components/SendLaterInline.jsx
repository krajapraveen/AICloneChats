/**
 * SendLaterInline — inline composer that lets an authenticated user seal an
 * emotional message addressed to a clone, deliverable to their own inbox at a
 * future time. Pre-fills with their last visitor message.
 *
 * Constitutional constraints embedded:
 *  - Only renders when the user is authenticated (check externally).
 *  - Single CTA, thesis vocabulary only.
 *  - Sends recipient_type="clone" with clone_id + source_conversation_id.
 *  - Confirmation copy says the system *delivers*, not chases.
 */
import { useState } from "react";
import { toast } from "sonner";
import api from "../lib/api";

function defaultDeliveryISO(daysAhead = 7) {
  const d = new Date(Date.now() + daysAhead * 24 * 60 * 60 * 1000);
  // Round to next 15-min boundary
  d.setMinutes(Math.ceil(d.getMinutes() / 15) * 15, 0, 0);
  return d.toISOString().slice(0, 16); // datetime-local format
}

export default function SendLaterInline({ cloneId, conversationId, prefillBody, onClose, onSent }) {
  const [open, setOpen] = useState(false);
  const [title, setTitle] = useState("");
  const [body, setBody] = useState(prefillBody || "");
  const [deliveryAt, setDeliveryAt] = useState(defaultDeliveryISO(7));
  const [submitting, setSubmitting] = useState(false);
  const [crisis, setCrisis] = useState("");

  const submit = async () => {
    if (!title.trim() || !body.trim()) { toast.error("Title and message required"); return; }
    if (!deliveryAt) { toast.error("Pick a delivery time"); return; }
    const dt = new Date(deliveryAt);
    if (isNaN(dt.getTime()) || dt.getTime() <= Date.now() + 30000) {
      toast.error("Delivery time must be at least 30s in the future"); return;
    }
    setSubmitting(true); setCrisis("");
    try {
      const r = await api.post("/delayed-messages", {
        title: title.trim(),
        message_body: body.trim(),
        emotional_category: "future_self",
        recipient_type: "clone",
        clone_id: cloneId,
        source_conversation_id: conversationId || undefined,
        delivery_time: dt.toISOString(),
        delivery_channel: "in_app",
      });
      if (r.data?.blocked && r.data?.self_harm_detected) {
        setCrisis(r.data.crisis_response || "Please reach out to someone you trust.");
      } else {
        toast.success("Sealed. The system will deliver it back to you.");
        onSent?.(r.data?.delayed_message);
        setOpen(false);
        setTitle(""); setBody("");
      }
    } catch (e) {
      const code = e?.response?.status;
      if (code === 503) toast.error("Delayed messages disabled for public users.");
      else toast.error(e?.response?.data?.detail || "Could not seal");
    } finally {
      setSubmitting(false);
    }
  };

  if (!open) {
    return (
      <button onClick={() => setOpen(true)} className="text-[10px] font-mono uppercase tracking-widest px-2 py-1 rounded-full border border-amber/40 text-amber hover:bg-amber/10 transition" data-testid="send-later-toggle">
        Send later
      </button>
    );
  }

  const minDt = new Date(Date.now() + 60_000).toISOString().slice(0, 16);

  return (
    <div className="brutal-card p-4 mt-3" data-testid="send-later-composer">
      {crisis && (
        <div className="brutal-card p-3 border-amber/40 bg-amber-500/10 mb-3" data-testid="send-later-crisis">
          <div className="text-amber font-mono text-[10px] uppercase tracking-widest mb-1">Pause</div>
          <div className="text-sm whitespace-pre-wrap">{crisis}</div>
        </div>
      )}
      <div className="text-[11px] font-mono uppercase tracking-widest text-muted mb-2">Write now. Receive later.</div>
      <input
        value={title}
        onChange={(e) => setTitle(e.target.value)}
        placeholder="Title — what is this for?"
        className="input-brutal w-full text-sm mb-2"
        maxLength={120}
        data-testid="send-later-title"
      />
      <textarea
        value={body}
        onChange={(e) => setBody(e.target.value)}
        placeholder="What do you want to read later…"
        rows={4}
        maxLength={4000}
        className="input-brutal w-full text-sm mb-2"
        data-testid="send-later-body"
      />
      <div className="flex flex-col sm:flex-row gap-2 mb-2">
        <input
          type="datetime-local"
          value={deliveryAt}
          onChange={(e) => setDeliveryAt(e.target.value)}
          min={minDt}
          className="input-brutal text-sm flex-1"
          data-testid="send-later-datetime"
        />
      </div>
      <div className="flex gap-2">
        <button onClick={submit} disabled={submitting} className="btn-brutal flex-1 disabled:opacity-50" data-testid="send-later-submit">
          {submitting ? "Sealing…" : "Seal & schedule →"}
        </button>
        <button onClick={() => { setOpen(false); onClose?.(); }} className="btn-ghost text-xs" data-testid="send-later-cancel">Cancel</button>
      </div>
      <div className="text-[10px] font-mono text-muted/70 mt-2">A message for when time matters. The system delivers; it does not chase.</div>
    </div>
  );
}
