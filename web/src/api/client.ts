export type Deployment = "central" | "site";

export type ApiErrorKind =
  | "unavailable"
  | "unauthorized"
  | "validation"
  | "server"
  | "unknown";

export class ApiError extends Error {
  constructor(
    public kind: ApiErrorKind,
    message: string,
    public status?: number,
    public requestId?: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

export interface ApiRequestOptions extends Omit<RequestInit, "signal"> {
  timeoutMs?: number;
  signal?: AbortSignal;
  retry?: boolean;
  query?: Record<string, string | number | boolean | undefined>;
}

export class ApiClient {
  constructor(
    private readonly baseUrl = "",
    private readonly headers: () => HeadersInit = () => ({}),
  ) {}

  async request<T>(path: string, options: ApiRequestOptions = {}): Promise<T> {
    const url = new URL(`${this.baseUrl}${path}`, window.location.origin);
    Object.entries(options.query ?? {}).forEach(
      ([key, value]) =>
        value !== undefined && url.searchParams.set(key, String(value)),
    );
    const timeout = new AbortController();
    const abort = () => timeout.abort();
    options.signal?.addEventListener("abort", abort, { once: true });
    const timer = window.setTimeout(
      () => timeout.abort(),
      options.timeoutMs ?? 10_000,
    );
    const { timeoutMs: _timeoutMs, query: _query, retry, ...init } = options;
    void _timeoutMs;
    void _query;
    try {
      const response = await fetch(url, {
        ...init,
        signal: timeout.signal,
        headers: {
          Accept: "application/json",
          ...this.headers(),
          ...init.headers,
        },
      });
      if (!response.ok) throw await this.error(response);
      if (response.status === 204) return undefined as T;
      return (await response.json()) as T;
    } catch (error) {
      if (
        retry &&
        (!options.method || options.method === "GET") &&
        !(error instanceof ApiError && error.kind !== "unavailable")
      ) {
        return this.request<T>(path, { ...options, retry: false });
      }
      if (error instanceof ApiError) throw error;
      throw new ApiError(
        "unavailable",
        error instanceof DOMException && error.name === "AbortError"
          ? "The service did not respond in time."
          : "The service is unavailable.",
      );
    } finally {
      window.clearTimeout(timer);
      options.signal?.removeEventListener("abort", abort);
    }
  }

  private async error(response: Response): Promise<ApiError> {
    let detail = response.statusText || "Request failed";
    try {
      const body = (await response.json()) as {
        detail?: string | Array<{ msg: string }> | { message?: string };
      };
      detail =
        typeof body.detail === "string"
          ? body.detail
          : Array.isArray(body.detail)
            ? body.detail.map((item) => item.msg).join("; ")
            : body.detail?.message || detail;
    } catch {
      /* non-JSON response */
    }
    const kind: ApiErrorKind =
      response.status === 401 || response.status === 403
        ? "unauthorized"
        : response.status === 400 ||
            response.status === 409 ||
            response.status === 422
          ? "validation"
          : response.status >= 500
            ? "server"
            : "unknown";
    return new ApiError(
      kind,
      detail,
      response.status,
      response.headers.get("x-request-id") ?? undefined,
    );
  }
}

export function createCentralClient(csrfToken: string | null = null) {
  return new ApiClient(
    "",
    (): HeadersInit => (csrfToken ? { "X-CSRF-Token": csrfToken } : {}),
  );
}

let siteCsrfToken: string | null = null;
export function setSiteCsrfToken(value:string|null){siteCsrfToken=value;}
export const siteClient = new ApiClient("",():HeadersInit=>siteCsrfToken?{"X-CSRF-Token":siteCsrfToken}:{});
