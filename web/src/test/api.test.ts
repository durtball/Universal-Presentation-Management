import { ApiClient } from "../api/client";

test("maps authorization errors to a structured API error", async () => {
  vi.spyOn(globalThis, "fetch").mockResolvedValue(
    new Response(
      JSON.stringify({ detail: "administrator authentication required" }),
      { status: 401, headers: { "Content-Type": "application/json" } },
    ),
  );
  await expect(new ApiClient().request("/api/test")).rejects.toMatchObject({
    kind: "unauthorized",
    status: 401,
  });
});
test("maps network failures to unavailable without leaking internals", async () => {
  vi.spyOn(globalThis, "fetch").mockRejectedValue(
    new TypeError("socket secret detail"),
  );
  await expect(new ApiClient().request("/api/test")).rejects.toMatchObject({
    kind: "unavailable",
    message: "The service is unavailable.",
  });
});
