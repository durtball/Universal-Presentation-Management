export const UPLOAD_RETRY_DELAYS_MS = [1_000, 2_000, 4_000, 8_000] as const;
let requestSequence = 0;

export class UploadError extends Error {
  constructor(
    message: string,
    public readonly category: string,
    public readonly status?: number,
    public readonly retryAfterMs?: number,
  ) { super(message); }
}

export async function uploadFile<T>({
  path, file, relativePath, query, csrf, progress, retrying,
}: {
  path: string; file: File; relativePath?: string;
  query?: Record<string, string | number | undefined>; csrf?: string | null;
  progress: (value: number) => void; retrying?: (count: number) => void;
}): Promise<T> {
  const idempotencyKey = `browser-upload-${Date.now()}-${requestSequence++}`;
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await attemptUpload<T>({ path, file, relativePath, query, csrf, progress, idempotencyKey });
    } catch (error) {
      if (!(error instanceof UploadError) || !isRetryable(error) || attempt >= UPLOAD_RETRY_DELAYS_MS.length) throw error;
      retrying?.(attempt + 1);
      const base = error.retryAfterMs ?? UPLOAD_RETRY_DELAYS_MS[attempt];
      await delay(base + Math.floor(Math.random() * Math.max(100, base / 4)));
    }
  }
}

function attemptUpload<T>({ path, file, relativePath, query, csrf, progress, idempotencyKey }: {
  path: string; file: File; relativePath?: string;
  query?: Record<string, string | number | undefined>; csrf?: string | null;
  progress: (value: number) => void; idempotencyKey: string;
}) {
  return new Promise<T>((resolve, reject) => {
    const url = new URL(path, window.location.origin);
    Object.entries(query ?? {}).forEach(([key, value]) => value !== undefined && url.searchParams.set(key, String(value)));
    const request = new XMLHttpRequest();
    request.open("POST", url);
    request.responseType = "json";
    request.setRequestHeader("X-UPM-Original-Filename", encodeURIComponent(file.name));
    if (relativePath) request.setRequestHeader("X-UPM-Source-Relative-Path", encodeURIComponent(relativePath));
    request.setRequestHeader("Content-Type", file.type || "application/octet-stream");
    request.setRequestHeader("Idempotency-Key", idempotencyKey);
    if (csrf) request.setRequestHeader("X-CSRF-Token", csrf);
    let watchdog = window.setTimeout(stalled, 120_000);
    const resetWatchdog = () => { window.clearTimeout(watchdog); watchdog = window.setTimeout(stalled, 120_000); };
    function stalled() { request.abort(); reject(new UploadError("Upload stopped making progress before durable staging completed.", "upload_transport_error", 504)); }
    request.upload.onprogress = (event) => { resetWatchdog(); progress(event.lengthComputable ? event.loaded / event.total * 100 : 0); };
    request.onerror = () => { window.clearTimeout(watchdog); reject(new UploadError("Upload transport failed. Check the connection and retry.", "upload_transport_error")); };
    request.onabort = () => window.clearTimeout(watchdog);
    request.onload = () => {
      window.clearTimeout(watchdog);
      if (request.status >= 200 && request.status < 300) resolve(request.response as T);
      else reject(responseError(request));
    };
    request.send(file);
  });
}

function responseError(request: XMLHttpRequest) {
  const detail = request.response?.detail;
  const message = typeof detail === "string" ? detail : detail?.message;
  if (request.status === 401 || request.status === 403) return new UploadError("Authentication failed. Sign in again and retry.", "auth_error", request.status);
  if (request.status === 413) return new UploadError("File is larger than the configured upload limit.", "size_limit", 413);
  if (request.status === 409) return new UploadError(message || "Duplicate upload or version conflict.", "duplicate_conflict", 409);
  if (request.status === 429) return new UploadError(message || "Server is busy; upload retry scheduled.", "server_pressure", 429, retryAfterMilliseconds(request.getResponseHeader("Retry-After")));
  if ([502, 503, 504].includes(request.status)) return new UploadError(message || "Service temporarily unavailable; upload retry scheduled.", "server_pressure", request.status);
  if (request.status === 500) return new UploadError(message || "Server processing failed. Technical details were logged.", "server_internal_error", 500);
  return new UploadError(message || `Upload failed (HTTP ${request.status}).`, "invalid_request", request.status);
}

function isRetryable(error: UploadError) {
  return isRetryableUploadStatus(error.status);
}

export const isRetryableUploadStatus = (status?: number) =>
  status === undefined || [429, 500, 502, 503, 504].includes(status);

const delay = (milliseconds: number) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

export function retryAfterMilliseconds(value: string | null): number | undefined {
  if (!value) return undefined;
  const seconds = Number(value);
  if (Number.isFinite(seconds) && seconds >= 0) return seconds * 1_000;
  const date = Date.parse(value);
  return Number.isNaN(date) ? undefined : Math.max(0, date - Date.now());
}
