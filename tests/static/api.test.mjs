import assert from "node:assert/strict";
import test from "node:test";

import { ApiError, api } from "../../app/static/api.js";

function withFetch(response, run) {
  const original = globalThis.fetch;
  globalThis.fetch = async () => response;
  return run().finally(() => {
    globalThis.fetch = original;
  });
}

test("api() joins a FastAPI 422 detail list into a readable message", async () => {
  const response = new Response(JSON.stringify({
    detail: [
      { loc: ["body", "assignments"], msg: "List should have at least 1 item after validation, not 0", type: "too_short" },
    ],
  }), { status: 422, headers: { "content-type": "application/json" } });

  await withFetch(response, () => (
    assert.rejects(
      () => api("/api/push/batch", { method: "POST", body: "{}" }),
      (error) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 422);
        assert.equal(error.message, "List should have at least 1 item after validation, not 0");
        return true;
      },
    )
  ));
});

test("api() joins several 422 detail entries with a space", () => {
  const response = new Response(JSON.stringify({
    detail: [
      { msg: "Field required" },
      { msg: "String should have at least 1 character" },
    ],
  }), { status: 422, headers: { "content-type": "application/json" } });

  return withFetch(response, () => (
    assert.rejects(
      () => api("/api/push/batch", { method: "POST", body: "{}" }),
      (error) => {
        assert.equal(error.message, "Field required String should have at least 1 character");
        return true;
      },
    )
  ));
});

test("api() falls back to JSON.stringify for a detail entry with no msg", () => {
  const response = new Response(JSON.stringify({ detail: [{ weird: true }] }), {
    status: 422,
    headers: { "content-type": "application/json" },
  });

  return withFetch(response, () => (
    assert.rejects(
      () => api("/api/push/batch", { method: "POST", body: "{}" }),
      (error) => {
        assert.equal(error.message, JSON.stringify({ weird: true }));
        return true;
      },
    )
  ));
});

test("api() falls back to the status line when detail is an empty list", () => {
  // FastAPI can answer a 503 with {"detail": []}. The array is truthy, but
  // joining zero entries yields "", and an ApiError with no message is
  // unreadable and, worse, indistinguishable from no error at all to a
  // caller that tests the message's truthiness.
  const response = new Response(JSON.stringify({ detail: [] }), {
    status: 503,
    headers: { "content-type": "application/json" },
  });

  return withFetch(response, () => (
    assert.rejects(
      () => api("/api/tonies"),
      (error) => {
        assert.ok(error instanceof ApiError);
        assert.equal(error.status, 503);
        assert.notEqual(error.message, "");
        assert.match(error.message, /503/);
        return true;
      },
    )
  ));
});

test("api() still reads a plain string detail", () => {
  const response = new Response(JSON.stringify({ detail: "Not found" }), {
    status: 404,
    headers: { "content-type": "application/json" },
  });

  return withFetch(response, () => (
    assert.rejects(
      () => api("/api/tonies"),
      (error) => {
        assert.equal(error.message, "Not found");
        return true;
      },
    )
  ));
});
