import React, { useEffect, useMemo, useState } from "react";
import Analyzer from "./pages/Analyzer";
import Chat from "./pages/Chat";
import { createSession, getSession } from "./api";

type Tab = "analyzer" | "chat";

function loadSessionId(): string | null {
  return sessionStorage.getItem("tdf_session_id");
}

function saveSessionId(id: string) {
  sessionStorage.setItem("tdf_session_id", id);
}

export default function App() {
  const [tab, setTab] = useState<Tab>("analyzer");
  const [sessionId, setSessionId] = useState<string | null>(() => loadSessionId());
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const shortId = useMemo(() => (sessionId ? sessionId.slice(0, 12) : "—"), [sessionId]);

  async function newSession() {
    setCreating(true);
    setError(null);
    try {
      const res = await createSession();
      setSessionId(res.session_id);
      saveSessionId(res.session_id);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setCreating(false);
    }
  }

  useEffect(() => {
    let cancelled = false;
    async function init() {
      setCreating(true);
      setError(null);
      try {
        const existing = loadSessionId();
        if (existing) {
          try {
            await getSession(existing);
            if (!cancelled) setSessionId(existing);
            return;
          } catch {
            sessionStorage.removeItem("tdf_session_id");
          }
        }
        const res = await createSession();
        if (cancelled) return;
        setSessionId(res.session_id);
        saveSessionId(res.session_id);
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e));
      } finally {
        if (!cancelled) setCreating(false);
      }
    }
    void init();
    return () => {
      cancelled = true;
    };
  }, []);

  return (
    <div className="app">
      <div className="topbar">
        <div className="brand">
          <div>TDF Risk Analyzer</div>
          <div className="pill">session: {shortId}</div>
        </div>
        <div className="nav">
          <button className={"btn " + (tab === "analyzer" ? "primary" : "")} onClick={() => setTab("analyzer")}>
            Analyzer
          </button>
          <button className={"btn " + (tab === "chat" ? "primary" : "")} onClick={() => setTab("chat")}>
            Chat
          </button>
          <button className="btn danger" onClick={newSession} disabled={creating}>
            {creating ? "Creating…" : "New session"}
          </button>
        </div>
      </div>
      <div className="content">
        {error ? (
          <div className="card">
            <div className="card-hd">Error</div>
            <div className="card-bd">
              <div className="muted">{error}</div>
              <div style={{ height: 10 }} />
              <button className="btn primary" onClick={newSession}>
                Retry
              </button>
            </div>
          </div>
        ) : null}

        {!error && sessionId ? (
          tab === "analyzer" ? (
            <Analyzer sessionId={sessionId} />
          ) : (
            <Chat sessionId={sessionId} />
          )
        ) : null}
      </div>
    </div>
  );
}
