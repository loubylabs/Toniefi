export class ApiError extends Error {
  constructor(message, { status = 0, details = null, url = "" } = {}) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.details = details;
    this.url = url;
  }
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
    const message = typeof body === "object" && body?.detail
      ? String(body.detail)
      : `${response.status} ${response.statusText || "Request failed"}`;
    throw new ApiError(message, {
      status: response.status,
      details: body,
      url: response.url || path,
    });
  }
  return body;
}
