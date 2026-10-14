/**
 * Backend client.
 *
 * The Pro token lives in localStorage. That is honest about what this is: a bearer
 * credential the server verifies by signature, not an identity. Anyone holding the
 * token gets Pro until it expires.
 */

export const API_BASE =
  process.env.NEXT_PUBLIC_API_BASE ?? "http://127.0.0.1:8000";

const TOKEN_KEY = "vd_pro_token";

export type JobMode = "video" | "audio" | "subtitle";

export type JobStatus =
  | "queued"
  | "probing"
  | "downloading"
  | "merging"
  | "converting"
  | "done"
  | "error"
  | "expired";

export type SummaryStatus =
  | "queued"
  | "extracting"
  | "transcribing"
  | "summarizing"
  | "done"
  | "error"
  | "expired";

export type MindMapStatus = "idle" | "queued" | "generating" | "done" | "error";

export interface SummaryJob {
  job_id: string;
  status: SummaryStatus;
  title: string;
  percent: number;
  error: string | null;
  transcript_source: "manual_caption" | "automatic_caption" | "local_whisper" | null;
  transcript_lang: string | null;
  ready: boolean;
  transcript_ready: boolean;
  mind_map_status: MindMapStatus;
  mind_map_percent: number;
  mind_map_error: string | null;
  mind_map_ready: boolean;
  created_at: number;
  updated_at: number;
  expires_at: number;
}

/** Server-validated outline tree. Ids and bounds are assigned by the backend. */
export interface MindMapNode {
  id: string;
  label: string;
  timestamp: number | null;
  children: MindMapNode[];
}

export interface MindMap {
  root: MindMapNode;
  node_count: number;
}

export type MindMapExportFormat = "json" | "markdown" | "opml";

export interface FormatOption {
  height: number;
  label: string;
  ext: string;
  filesize: number | null;
  filesize_estimated: boolean;
  /** False when only AV1 exists at this height, which many phones cannot play. */
  compatible: boolean;
  drm: boolean;
}

export interface ProbeResult {
  url: string;
  title: string;
  duration: number | null;
  thumbnail: string | null;
  uploader: string | null;
  extractor: string | null;
  is_live: boolean;
  live_status: string | null;
  formats: FormatOption[];
  subtitles: string[];
  auto_subtitles: string[];
  has_subtitles: boolean;
}

export interface Job {
  job_id: string;
  status: JobStatus;
  mode: JobMode;
  title: string | null;
  thumbnail: string | null;
  percent: number;
  downloaded_bytes: number;
  total_bytes: number | null;
  speed: number | null;
  eta: number | null;
  error: string | null;
  filename: string | null;
  filesize: number | null;
  ready: boolean;
}

export interface Limits {
  is_pro: boolean;
  max_height: number;
  batch_limit: number;
  free_max_height: number;
  pro_max_height: number;
  free_batch_limit: number;
  pro_batch_limit: number;
  ai_enabled: boolean;
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
    this.name = "ApiError";
  }
}

/** 402 means "this needs Pro" -- callers use it to open the upgrade dialog. */
export const isUpgradeRequired = (e: unknown) =>
  e instanceof ApiError && e.status === 402;

export function getToken(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(TOKEN_KEY);
}

export function setToken(token: string) {
  window.localStorage.setItem(TOKEN_KEY, token);
}

export function clearToken() {
  window.localStorage.removeItem(TOKEN_KEY);
}

async function errorFromResponse(res: Response): Promise<ApiError> {
  let detail = `请求失败 (${res.status})`;
  try {
    const body = await res.json();
    if (typeof body?.detail === "string") detail = body.detail;
    else if (Array.isArray(body?.detail)) detail = "请求参数不正确";
  } catch {
    /* non-JSON error body */
  }
  return new ApiError(res.status, detail);
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const token = getToken();
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...((init?.headers as Record<string, string>) ?? {}),
  };
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { ...init, headers });
  } catch {
    throw new ApiError(0, "无法连接服务器，请确认后端已启动");
  }

  if (!res.ok) {
    throw await errorFromResponse(res);
  }

  return res.status === 204 ? (undefined as T) : ((await res.json()) as T);
}

async function requestBlob(path: string): Promise<Blob> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers });
  } catch {
    throw new ApiError(0, "无法连接服务器，请确认后端已启动");
  }

  if (!res.ok) {
    throw await errorFromResponse(res);
  }
  return res.blob();
}

async function requestText(path: string): Promise<string> {
  const token = getToken();
  const headers: Record<string, string> = {};
  if (token) headers.Authorization = `Bearer ${token}`;

  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, { headers });
  } catch {
    throw new ApiError(0, "无法连接服务器，请确认后端已启动");
  }

  if (!res.ok) throw await errorFromResponse(res);
  return res.text();
}

export const api = {
  limits: () => request<Limits>("/api/limits"),

  probe: (url: string) =>
    request<ProbeResult>("/api/probe", {
      method: "POST",
      body: JSON.stringify({ url }),
    }),

  createJob: (input: {
    url: string;
    mode?: JobMode;
    height?: number | null;
    subtitle_lang?: string | null;
  }) =>
    request<Job>("/api/jobs", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  batch: (input: { urls: string[]; mode?: JobMode; height?: number | null }) =>
    request<{ jobs: Job[]; rejected: { url: string; error: string }[] }>(
      "/api/batch",
      { method: "POST", body: JSON.stringify(input) },
    ),

  job: (id: string) => request<Job>(`/api/jobs/${id}`),

  createSummaryJob: (input: {
    url: string;
    title?: string | null;
    source_lang: string;
    output_lang: "zh-CN" | "en";
  }) =>
    request<SummaryJob>("/api/ai/jobs", {
      method: "POST",
      body: JSON.stringify(input),
    }),

  summaryJob: (id: string) => request<SummaryJob>(`/api/ai/jobs/${id}`),

  summaryResult: (id: string) =>
    requestText(`/api/ai/jobs/${id}/result`),

  summaryExport: (id: string) => requestBlob(`/api/ai/jobs/${id}/export`),

  createMindMap: (id: string) =>
    request<SummaryJob>(`/api/ai/jobs/${id}/mind-map`, { method: "POST" }),

  mindMap: (id: string) => request<MindMap>(`/api/ai/jobs/${id}/mind-map`),

  mindMapExport: (id: string, format: MindMapExportFormat) =>
    requestBlob(`/api/ai/jobs/${id}/mind-map/export?format=${format}`),

  redeem: (code: string) =>
    request<{ token: string; tier: string; expires_in: number }>(
      "/api/redeem",
      { method: "POST", body: JSON.stringify({ code }) },
    ),

  aiTranslate: (url: string, target_lang = "简体中文") =>
    request<{
      srt: string;
      source_lang: string;
      target_lang: string;
      model: string;
    }>("/api/ai/translate", {
      method: "POST",
      body: JSON.stringify({ url, target_lang }),
    }),

  /**
   * Same-origin-style download URL served by our own backend.
   *
   * Deliberately not the platform's CDN URL: those are IP/token-bound and 403 from
   * the user's device, HLS/DASH are segmented manifests a browser cannot save as one
   * file, and the HTML download attribute is ignored cross-origin.
   */
  fileUrl: (id: string) => `${API_BASE}/api/jobs/${id}/file`,

  /** Same bytes as fileUrl, served inline so a <video> element can seek it. */
  playUrl: (id: string) => `${API_BASE}/api/jobs/${id}/play`,
};

/** Poll until the job settles. Simpler than SSE and immune to proxy buffering. */
export function pollJob(
  id: string,
  onUpdate: (job: Job) => void,
  intervalMs = 1000,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout>;

  const tick = async () => {
    if (stopped) return;
    try {
      const job = await api.job(id);
      if (stopped) return;
      onUpdate(job);
      if (job.status === "done" || job.status === "error" || job.status === "expired") {
        return;
      }
    } catch (err) {
      if (stopped) return;
      // A 404 means the job expired or the server restarted; stop rather than spin.
      if (err instanceof ApiError && err.status === 404) {
        onUpdate({
          job_id: id,
          status: "expired",
          mode: "video",
          title: null,
          thumbnail: null,
          percent: 0,
          downloaded_bytes: 0,
          total_bytes: null,
          speed: null,
          eta: null,
          error: "任务已过期，请重新下载",
          filename: null,
          filesize: null,
          ready: false,
        });
        return;
      }
    }
    timer = setTimeout(tick, intervalMs);
  };

  void tick();
  return () => {
    stopped = true;
    clearTimeout(timer);
  };
}

export function pollSummaryJob(
  id: string,
  onUpdate: (job: SummaryJob) => void,
  intervalMs = 1000,
  onError?: (error: ApiError) => void,
  onAuthRequired?: (error: ApiError) => void,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
  };

  const tick = async () => {
    if (stopped) return;
    try {
      const job = await api.summaryJob(id);
      if (stopped) return;
      onUpdate(job);
      if (job.status === "done" || job.status === "error" || job.status === "expired") {
        stop();
        return;
      }
    } catch (err) {
      if (stopped) return;
      if (err instanceof ApiError && err.status === 404) {
        stop();
        onUpdate({
          job_id: id,
          status: "expired",
          title: "",
          percent: 0,
          error: "总结任务已过期，请重新生成",
          transcript_source: null,
          transcript_lang: null,
          ready: false,
          transcript_ready: false,
          mind_map_status: "idle",
          mind_map_percent: 0,
          mind_map_error: null,
          mind_map_ready: false,
          created_at: 0,
          updated_at: 0,
          expires_at: 0,
        });
        return;
      }
      if (err instanceof ApiError && err.status === 402) {
        stop();
        onAuthRequired?.(err);
        return;
      }
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        stop();
        onError?.(err);
        return;
      }
    }
    if (!stopped) timer = setTimeout(tick, intervalMs);
  };

  void tick();
  return stop;
}

/**
 * Poll a summary job until its mind map settles.
 *
 * Mind map state rides on the summary job, so this reads the same status endpoint
 * and stops on the mind map's terminal states rather than the summary's.
 */
export function pollMindMap(
  id: string,
  onUpdate: (job: SummaryJob) => void,
  intervalMs = 1000,
  onError?: (error: ApiError) => void,
): () => void {
  let stopped = false;
  let timer: ReturnType<typeof setTimeout> | undefined;

  const stop = () => {
    if (stopped) return;
    stopped = true;
    if (timer !== undefined) clearTimeout(timer);
  };

  const tick = async () => {
    if (stopped) return;
    try {
      const job = await api.summaryJob(id);
      if (stopped) return;
      onUpdate(job);
      if (job.mind_map_status === "done" || job.mind_map_status === "error") {
        stop();
        return;
      }
    } catch (err) {
      if (stopped) return;
      if (err instanceof ApiError && err.status === 404) {
        stop();
        onError?.(new ApiError(404, "总结任务已过期，请重新生成"));
        return;
      }
      if (err instanceof ApiError && err.status >= 400 && err.status < 500) {
        stop();
        onError?.(err);
        return;
      }
    }
    if (!stopped) timer = setTimeout(tick, intervalMs);
  };

  void tick();
  return stop;
}

// --- formatting helpers -----------------------------------------------------

export function formatBytes(bytes: number | null | undefined): string {
  if (!bytes || bytes <= 0) return "--";
  const units = ["B", "KB", "MB", "GB"];
  let value = bytes;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  return `${value.toFixed(value >= 100 || unit === 0 ? 0 : 1)} ${units[unit]}`;
}

export function formatSpeed(bytesPerSecond: number | null): string {
  if (!bytesPerSecond || bytesPerSecond <= 0) return "--";
  return `${formatBytes(bytesPerSecond)}/s`;
}

export function formatDuration(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "--";
  const total = Math.round(seconds);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = (n: number) => String(n).padStart(2, "0");
  return h > 0 ? `${h}:${pad(m)}:${pad(s)}` : `${m}:${pad(s)}`;
}

export function formatEta(seconds: number | null): string {
  if (!seconds || seconds <= 0) return "--";
  if (seconds < 60) return `${Math.round(seconds)} 秒`;
  return `${Math.floor(seconds / 60)} 分 ${Math.round(seconds % 60)} 秒`;
}

/** iOS Safari needs different save guidance, so detect it for the hint. */
export function isIOS(): boolean {
  if (typeof navigator === "undefined") return false;
  const ua = navigator.userAgent;
  return (
    /iPad|iPhone|iPod/.test(ua) ||
    (ua.includes("Macintosh") && "ontouchend" in document)
  );
}
