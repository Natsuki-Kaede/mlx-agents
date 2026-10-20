export type Upload = { id: string; kind: "image" | "video"; url: string };
export type Status = { loaded: string | null; loaded_kind: "chat" | "image" | null; busy: string | null; pending: number; active_gb: number };
export type ModelInfo = {
  id: string;
  kind: "chat" | "image";
  runtime: "mlx-lm" | "mlx-vlm" | "mflux";
  vision: boolean;
  model_type: string;
  bits: number | null;
  size_gb: number;
  supported: boolean;
  note: string;
};
export type ModelDownload = { repo: string; id: string; status: "running" | "done" | "error"; total: number; done: number; error: string | null };
export type ModelList = { models: ModelInfo[]; default_chat: string | null; default_image: string | null; downloads: ModelDownload[] };
export type Reasoning = "off" | "low" | "medium" | "high";
export type VlmParams = {
  max_tokens: number;
  temperature: number;
  repetition_penalty: number;
  fps: number;
  max_frames: number;
  reasoning: Reasoning;
};
export type TokenCounts = { thinking: number; answer: number };
export type VlmStats = {
  seconds: number;
  load_seconds: number;
  prompt_tokens: number;
  generation_tokens: number;
  generation_tps: number;
  peak_mlx_gb: number;
};
export type OutputMeta = {
  file: string;
  url: string;
  prompt?: string;
  width?: number;
  height?: number;
  steps?: number;
  seed?: number;
  step_cache?: number;
  init_image?: string | null;
  image_strength?: number | null;
  created?: string;
  seconds?: number;
  encode_seconds?: number;
  peak_mlx_gb?: number;
  source?: Record<string, unknown> | null;
};
export type Job = {
  id: string;
  kind: string;
  status: "queued" | "running" | "done" | "error";
  step: number;
  total: number;
  phase: string;
  result: OutputMeta | null;
  error: string | null;
  elapsed: number;
};

/** Per-chat switches: which tool groups the agent may use, and which approvals it may skip. */
export type Permissions = { web: boolean; image: boolean; files: boolean; exec: boolean };
export type ApprovalKind = "write" | "exec";
export type ChatSettings = Permissions & { autoApprove?: ApprovalKind[]; model?: string | null };

export type PlanStep = { text: string; status: "pending" | "in_progress" | "done" };
export type ToolEvent = {
  id: string;
  name: string;
  args: Record<string, unknown>;
  status: "running" | "done" | "error" | "awaiting_approval" | "denied";
  approval?: { summary: string; detail: string };
  decision?: string;
  results?: { title: string; url: string; snippet: string }[];
  url?: string;
  title?: string;
  chars?: number;
  truncated?: boolean;
  path?: string;
  lines?: number;
  count?: number;
  files?: number;
  output?: string;
  exit_code?: number | null;
  stopped?: string | null;
  memory?: { id: string; text: string };
  job?: string;
  image?: string;
  file?: string;
  width?: number;
  height?: number;
  steps?: number;
  seed?: number;
  seconds?: number;
  reference?: string | null;
  strength?: number | null;
  error?: string;
};

/** Turns sent through the old composer modes (before the agent took them over) are still rendered. */
export type LegacyMode = "chat" | "image" | "describe";
export type DescribeCardData = {
  status: "describing" | "merging" | "done" | "error";
  source: Upload;
  fields?: Record<string, string> | null;
  raw?: string;
  prompt?: string;
  error?: string;
};
export type ChatTurn = {
  role: "user" | "assistant";
  content: string;
  mode?: LegacyMode;
  describe?: DescribeCardData;
  thinking?: string;
  tokens?: TokenCounts;
  tools?: ToolEvent[];
  plan?: PlanStep[];
  attachments?: Upload[];
  stats?: (VlmStats & { tokens?: TokenCounts }) | null;
};
export type SavedChat = { title: string; turns: ChatTurn[]; settings?: Partial<ChatSettings> };
export type ChatSummary = { id: string; title: string; updated: number; turns: number };
export type RunInfo = { id: string; chat_id: string; status: string; events: number; created: number };
export type RunEvent = { seq: number } & (
  | { type: "status"; status: string; note?: string }
  | { type: "thinking" | "answer"; text: string; tokens: TokenCounts }
  | { type: "tool"; tool: ToolEvent }
  | { type: "plan"; plan: PlanStep[] }
  | { type: "auto_approve"; auto_approve: ApprovalKind[] }
  | { type: "end"; status: "done" | "error" | "cancelled"; stats: VlmStats & { tokens?: TokenCounts }; error: string | null }
);
export type Memory = { id: string; text: string; created: number };
export type AppSettings = { workspace: string; workspace_resolved: string; chat_model?: string; image_model?: string };

export const FIELD_LABELS: Record<string, string> = {
  subject: "主体",
  appearance: "外观",
  action: "动作",
  scene: "场景",
  composition: "构图与机位",
  lighting: "光线",
  color: "色彩",
  style: "风格",
  motion: "运动",
  camera: "运镜",
  timeline: "时间线",
};

async function request<T>(url: string, init?: RequestInit): Promise<T> {
  const res = await fetch(url, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail ?? body);
    } catch {
      /* non-JSON error body: keep the status text */
    }
    throw new Error(`${res.status} ${detail}`);
  }
  return res.json() as Promise<T>;
}

const send = (method: string, body: unknown): RequestInit => ({
  method,
  headers: { "Content-Type": "application/json" },
  body: JSON.stringify(body),
});
const json = (body: unknown) => send("POST", body);

export const api = {
  status: () => request<Status>("/api/status"),
  gallery: () => request<OutputMeta[]>("/api/gallery"),
  job: (id: string) => request<Job>(`/api/jobs/${id}`),
  upload: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Upload>("/api/uploads", { method: "POST", body: form });
  },
  uploadFromOutput: (file: string) => request<Upload>("/api/uploads/from-output", json({ file })),
  chats: () => request<ChatSummary[]>("/api/chats"),
  chat: (id: string) => request<SavedChat & { id: string }>(`/api/chats/${id}`),
  chatRun: (id: string) => request<RunInfo | null>(`/api/chats/${id}/run`),
  patchChatSettings: (id: string, patch: Partial<ChatSettings>) =>
    request<Partial<ChatSettings>>(`/api/chats/${id}/settings`, send("PATCH", patch)),
  deleteChat: (id: string) => request<{ id: string }>(`/api/chats/${id}`, { method: "DELETE" }),
  startRun: (body: {
    chat_id: string;
    content: string;
    attachments: string[];
    params: VlmParams;
    permissions: Permissions;
    auto_approve: ApprovalKind[];
    system: string;
    model: string | null;
  }) => request<RunInfo>("/api/runs", json(body)),
  cancelRun: (id: string) => request<{ id: string }>(`/api/runs/${id}/cancel`, { method: "POST" }),
  approve: (id: string, tool_id: string, decision: "allow" | "always" | "deny") =>
    request<{ id: string }>(`/api/runs/${id}/approvals`, json({ tool_id, decision })),
  memories: () => request<Memory[]>("/api/memory"),
  addMemory: (text: string) => request<Memory>("/api/memory", json({ text })),
  deleteMemory: (id: string) => request<{ id: string }>(`/api/memory/${id}`, { method: "DELETE" }),
  settings: () => request<AppSettings>("/api/settings"),
  saveSettings: (patch: { workspace?: string; chat_model?: string; image_model?: string }) => request<AppSettings>("/api/settings", send("PUT", patch)),
  models: () => request<ModelList>("/api/models"),
  downloadModel: (repo: string) => request<ModelDownload>("/api/models/download", json({ repo })),
};

/** Read a text/event-stream response body and yield each `data:` payload as JSON (comments are skipped). */
async function* readSse<T>(res: Response): AsyncGenerator<T> {
  if (!res.ok || !res.body) throw new Error(`${res.status} ${res.statusText}`);
  const reader = res.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) return;
    buffer += value;
    let cut: number;
    while ((cut = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, cut);
      buffer = buffer.slice(cut + 2);
      const line = chunk.split("\n").find((l) => l.startsWith("data: "));
      if (line) yield JSON.parse(line.slice(6)) as T;
    }
  }
}

/** A run's events from `after` on; the stream ends with the run's "end" event. */
export async function* runEvents(id: string, after: number, signal?: AbortSignal): AsyncGenerator<RunEvent> {
  const res = await fetch(`/api/runs/${id}/events?after=${after}`, { signal });
  yield* readSse<RunEvent>(res);
}

export async function* watchJob(id: string, signal?: AbortSignal): AsyncGenerator<Job> {
  const res = await fetch(`/api/jobs/${id}/events`, { signal });
  yield* readSse<Job>(res);
}
