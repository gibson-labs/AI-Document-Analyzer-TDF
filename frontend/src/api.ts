export type ApiError = { error: { code: string; message: string; hint?: string | null } };

async function parseJson<T>(res: Response): Promise<T> {
  const text = await res.text();
  try {
    return JSON.parse(text) as T;
  } catch {
    throw new Error(`Non-JSON response (${res.status}): ${text.slice(0, 200)}`);
  }
}

export async function api<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    const body = await parseJson<ApiError>(res).catch(() => null);
    const msg = body?.error?.message ?? `Request failed (${res.status})`;
    const hint = body?.error?.hint ? `\n\nHint: ${body.error.hint}` : "";
    const err = new Error(`${msg}${hint}`);
    (err as any).code = body?.error?.code;
    (err as any).status = res.status;
    throw err;
  }
  return parseJson<T>(res);
}

export type CreateSessionResponse = { session_id: string };
export type DocumentInfo = { name: string; size_bytes: number; modified_utc: string };
export type SessionStatusResponse = {
  session_id: string;
  company?: string | null;
  created_utc?: string | null;
  last_activity_utc?: string | null;
  index_dirty: boolean;
  documents: DocumentInfo[];
};

export type IndexStatusResponse = { status: "missing" | "dirty" | "ready"; index_dirty: boolean; document_count: number };

export type AnalyzeRequest = {
  company: string;
  mode: "fedex" | "weighted" | "memo";
  summarizer_spec?: string | null;
  analyzer_spec?: string | null;
};
export type AnalyzeResponse = { output_id: string; markdown: string };

export type ChatRequest = {
  company: string;
  message: string;
  conversation_id?: string;
  summarizer_spec?: string | null;
  analyzer_spec?: string | null;
};
export type ChatResponse = { message: string; response: string; conversation_id: string };

export async function createSession(company?: string): Promise<CreateSessionResponse> {
  return api<CreateSessionResponse>("/api/session", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ company: company ?? null }),
  });
}

export async function getSession(sessionId: string): Promise<SessionStatusResponse> {
  return api<SessionStatusResponse>(`/api/session/${sessionId}`);
}

export async function getIndexStatus(sessionId: string): Promise<IndexStatusResponse> {
  return api<IndexStatusResponse>(`/api/session/${sessionId}/index/status`);
}

export async function uploadDocuments(sessionId: string, files: File[]): Promise<SessionStatusResponse> {
  const form = new FormData();
  for (const f of files) form.append("files", f);
  return api<SessionStatusResponse>(`/api/session/${sessionId}/documents`, { method: "POST", body: form });
}

export async function deleteDocument(sessionId: string, filename: string): Promise<SessionStatusResponse> {
  return api<SessionStatusResponse>(`/api/session/${sessionId}/documents/${encodeURIComponent(filename)}`, { method: "DELETE" });
}

export async function analyze(sessionId: string, body: AnalyzeRequest): Promise<AnalyzeResponse> {
  return api<AnalyzeResponse>(`/api/session/${sessionId}/analyze`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
}

export async function chat(sessionId: string, body: ChatRequest): Promise<ChatResponse> {
  return api<ChatResponse>(`/api/session/${sessionId}/chat`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ conversation_id: "default", ...body }),
  });
}
