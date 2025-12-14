import React, { useEffect, useMemo, useRef, useState } from "react";
import { chat, getIndexStatus } from "../api";

type Props = { sessionId: string };

type Msg = { role: "user" | "assistant"; content: string };

export default function Chat({ sessionId }: Props) {
  const [company, setCompany] = useState("Acme");
  const [status, setStatus] = useState<"missing" | "dirty" | "ready" | "loading">("loading");
  const [messages, setMessages] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [sending, setSending] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const endRef = useRef<HTMLDivElement | null>(null);

  const canChat = useMemo(() => status !== "missing", [status]);

  async function refreshStatus() {
    try {
      const idx = await getIndexStatus(sessionId);
      setStatus(idx.status);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setStatus("missing");
    }
  }

  useEffect(() => {
    setMessages([]);
    setErr(null);
    setStatus("loading");
    void refreshStatus();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages.length]);

  async function send() {
    if (!input.trim() || sending) return;
    setSending(true);
    setErr(null);
    const text = input.trim();
    setInput("");
    setMessages((m) => [...m, { role: "user", content: text }]);
    try {
      const res = await chat(sessionId, { company, message: text });
      setMessages((m) => [...m, { role: "assistant", content: res.response }]);
      await refreshStatus();
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
      setMessages((m) => [...m, { role: "assistant", content: "Sorry — I hit an error processing that request." }]);
    } finally {
      setSending(false);
    }
  }

  return (
    <div className="layout" style={{ gridTemplateColumns: "360px 1fr" }}>
      <div className="card">
        <div className="card-hd">
          <div>Chat settings</div>
          <div className="pill">{status}</div>
        </div>
        <div className="card-bd">
          <div className="row">
            <div>
              <label>Company</label>
              <input value={company} onChange={(e) => setCompany(e.target.value)} />
            </div>
            <div className="muted">
              {status === "missing"
                ? "Upload documents first in Analyzer."
                : "Ask follow-ups grounded in the uploaded documents. Citations show up inline as “Source: …” blocks."}
            </div>
            {err ? <div className="muted" style={{ color: "var(--danger)" }}>{err}</div> : null}
          </div>
        </div>
      </div>

      <div className="card">
        <div className="card-hd">
          <div>Conversation</div>
          <div className="pill">default</div>
        </div>
        <div className="card-bd">
          <div style={{ minHeight: 360, display: "flex", flexDirection: "column", gap: 10 }}>
            {messages.length === 0 ? (
              <div className="muted">No messages yet. Ask something like “Summarize key safety risks” or “What’s the risk band and why?”</div>
            ) : null}
            {messages.map((m, idx) => (
              <div
                key={idx}
                style={{
                  alignSelf: m.role === "user" ? "flex-end" : "flex-start",
                  maxWidth: "92%",
                  whiteSpace: "pre-wrap",
                  padding: "10px 12px",
                  borderRadius: 14,
                  border: "1px solid var(--border)",
                  background: m.role === "user" ? "rgba(124, 92, 255, 0.16)" : "rgba(255, 255, 255, 0.05)",
                }}
              >
                {m.content}
              </div>
            ))}
            <div ref={endRef} />
          </div>

          <div style={{ height: 14 }} />
          <div style={{ display: "flex", gap: 10 }}>
            <textarea
              value={input}
              placeholder={canChat ? "Ask a question about the uploaded documents…" : "Upload documents first…"}
              onChange={(e) => setInput(e.target.value)}
              disabled={!canChat || sending}
              onKeyDown={(e) => {
                if (e.key === "Enter" && !e.shiftKey) {
                  e.preventDefault();
                  void send();
                }
              }}
            />
            <button className="btn primary" onClick={send} disabled={!canChat || sending}>
              {sending ? "Sending…" : "Send"}
            </button>
          </div>
          <div className="muted" style={{ marginTop: 8 }}>
            Tip: Press Enter to send, Shift+Enter for a new line.
          </div>
        </div>
      </div>
    </div>
  );
}
