import React, { useEffect, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import {
  AnalyzeResponse,
  IndexStatusResponse,
  SessionStatusResponse,
  analyze,
  deleteDocument,
  getIndexStatus,
  getSession,
  uploadDocuments,
} from "../api";

type Props = { sessionId: string };

export default function Analyzer({ sessionId }: Props) {
  const [company, setCompany] = useState("Acme");
  const [mode, setMode] = useState<"fedex" | "weighted" | "memo">("fedex");
  const [session, setSession] = useState<SessionStatusResponse | null>(null);
  const [indexStatus, setIndexStatus] = useState<IndexStatusResponse | null>(null);
  const [busy, setBusy] = useState(false);
  const [running, setRunning] = useState(false);
  const [result, setResult] = useState<AnalyzeResponse | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const exportHref = useMemo(() => {
    if (!result) return null;
    return `/api/session/${sessionId}/outputs/${result.output_id}.md`;
  }, [result, sessionId]);

  async function refresh() {
    try {
      const [s, idx] = await Promise.all([getSession(sessionId), getIndexStatus(sessionId)]);
      setSession(s);
      setIndexStatus(idx);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    }
  }

  useEffect(() => {
    setResult(null);
    setErr(null);
    void refresh();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId]);

  async function onUpload(files: File[]) {
    setBusy(true);
    setErr(null);
    try {
      const s = await uploadDocuments(sessionId, files);
      setSession(s);
      const idx = await getIndexStatus(sessionId);
      setIndexStatus(idx);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onDelete(filename: string) {
    setBusy(true);
    setErr(null);
    try {
      const s = await deleteDocument(sessionId, filename);
      setSession(s);
      const idx = await getIndexStatus(sessionId);
      setIndexStatus(idx);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }

  async function onAnalyze() {
    setRunning(true);
    setErr(null);
    try {
      const res = await analyze(sessionId, { company, mode });
      setResult(res);
      const idx = await getIndexStatus(sessionId);
      setIndexStatus(idx);
    } catch (e) {
      setErr(e instanceof Error ? e.message : String(e));
    } finally {
      setRunning(false);
    }
  }

  return (
    <div className="layout">
      <div className="card">
        <div className="card-hd">
          <div>Session</div>
          <div className="pill">{indexStatus?.status ?? "…"}</div>
        </div>
        <div className="card-bd">
          <div className="row">
            <div>
              <label>Company</label>
              <input value={company} onChange={(e) => setCompany(e.target.value)} />
            </div>
            <div>
              <label>Mode</label>
              <select value={mode} onChange={(e) => setMode(e.target.value as any)}>
                <option value="fedex">FedEx review (default)</option>
                <option value="memo">Credit memo</option>
                <option value="weighted">Weighted (matrix)</option>
              </select>
              <div className="muted" style={{ marginTop: 6 }}>
                Weighted requires a server-side decision matrix at <span style={{ fontFamily: "var(--mono)" }}>files/Decision Matrix&apos;s.xlsx</span> (or{" "}
                <span style={{ fontFamily: "var(--mono)" }}>TDF_DECISION_MATRIX_PATH</span>).
              </div>
            </div>

            <div className="dropzone">
              <label>Upload documents</label>
              <input
                type="file"
                multiple
                onChange={(e) => {
                  const f = Array.from(e.target.files ?? []);
                  if (f.length) void onUpload(f);
                  e.currentTarget.value = "";
                }}
                disabled={busy}
              />
              <div className="muted" style={{ marginTop: 6 }}>
                Supported: PDF, JPG/PNG, XLSX/XLS
              </div>
            </div>

            {err ? <div className="muted" style={{ color: "var(--danger)" }}>{err}</div> : null}

            <div>
              <button className="btn primary" onClick={onAnalyze} disabled={running}>
                {running ? "Running…" : "Run analysis"}
              </button>
              {exportHref ? (
                <a className="btn" style={{ marginLeft: 8, textDecoration: "none", display: "inline-block" }} href={exportHref}>
                  Download .md
                </a>
              ) : null}
            </div>
          </div>

          <div style={{ height: 14 }} />
          <div className="muted">Documents</div>
          <ul className="filelist">
            {session?.documents?.length ? (
              session.documents.map((d) => (
                <li key={d.name}>
                  <div className="filename">{d.name}</div>
                  <button className="btn" onClick={() => onDelete(d.name)} disabled={busy}>
                    Remove
                  </button>
                </li>
              ))
            ) : (
              <li>
                <div className="muted">No documents uploaded yet.</div>
              </li>
            )}
          </ul>
        </div>
      </div>

      <div className="card">
        <div className="card-hd">
          <div>Report</div>
          {result ? <div className="pill">{result.output_id}</div> : <div className="pill">—</div>}
        </div>
        <div className="card-bd">
          {!result ? (
            <div className="muted">Upload documents, then run analysis. The report renders as Markdown.</div>
          ) : (
            <div className="report">
              <ReactMarkdown remarkPlugins={[remarkGfm]}>{result.markdown}</ReactMarkdown>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
