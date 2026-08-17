import { describe, expect, it } from "vitest";
import { isRetryableUploadStatus, retryAfterMilliseconds, UPLOAD_RETRY_DELAYS_MS } from "../api/upload";

describe("upload retry policy", () => {
  it("uses bounded exponential backoff", () => {
    expect(UPLOAD_RETRY_DELAYS_MS).toEqual([1_000, 2_000, 4_000, 8_000]);
  });
  it("retries only transport and transient server pressure", () => {
    expect([undefined, 429, 500, 502, 503, 504].every(isRetryableUploadStatus)).toBe(true);
    expect([400, 401, 403, 409, 413, 422].some(isRetryableUploadStatus)).toBe(false);
  });
  it("honors Retry-After seconds", () => {
    expect(retryAfterMilliseconds("3")).toBe(3_000);
  });
});
