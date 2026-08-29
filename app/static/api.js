export class ApiError extends Error {
  constructor(message, { status = 0, details = null, url = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
    this.url = url;
  }
}

function formatDetail(detail) {
  // FastAPI's 422 `detail` is a list of {msg, loc, ...} objects, not a string.
  // String(detail) on an array joins each element with its own toString, and
  // a plain object's toString is "[object Object]", so an unhandled
  // validation error reads as "[object Object]" instead of the message.
  if (Array.isArray(detail)) {
    return detail
      .map((entry) => (entry && typeof entry === "object" && typeof entry.msg === "string" ? entry.msg : JSON.stringify(entry)))
      .join(" ");
  }
  return String(detail);
}

function requestHeaders(options) {
  const headers = new Headers(options.headers || {});
  if (options.body != null && !(options.body instanceof FormData) && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  headers.set("Accept", "application/json");
  return headers;
}

async function responseBody(response) {
  if (response.status === 204) return null;
  const text = await response.text();
  if (!text) return null;
  const contentType = response.headers.get("content-type") || "";
  if (!contentType.includes("application/json")) return text;
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new ApiError("The server returned an unreadable response.", {
      status: response.status,
      details: text,
      url: response.url,
    });
  }
}

export async function api(path, options = {}) {
  let response;
  try {
    response = await fetch(path, {
      ...options,
      headers: requestHeaders(options),
    });
  } catch (error) {
    throw new ApiError("TonieFi could not reach the server. Check the connection and try again.", {
      details: error,
      url: path,
    });
  }

  const body = await responseBody(response);
  if (!response.ok) {
    const statusMessage = `${response.status} ${response.statusText || "Request failed"}`;
    // `detail` can be present and still carry nothing readable, e.g. FastAPI's
    // `{"detail": []}`: the array is truthy, but joining zero entries yields
    // "". An ApiError with an empty message is unhelpful everywhere it
    // surfaces, so a blank join falls back to the status line rather than
    // handing every caller an empty string to display or to test for failure.
    const detailMessage = typeof body === "object" && body?.detail ? formatDetail(body.detail) : "";
    throw new ApiError(detailMessage || statusMessage, {
      status: response.status,
      details: body,
      url: response.url || path,
    });
  }
  return body;
}

export function scopeRequest(request, signal) {
  if (!signal) return request;
  return (path, options = {}) => request(path, { ...options, signal });
}
