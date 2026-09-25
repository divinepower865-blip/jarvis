/** Headless integration helper for your own UI. No provider credentials here. */
export class JarvisClient {
  constructor(baseUrl, localApiToken) {
    this.baseUrl = baseUrl.replace(/\/$/, "");
    this.token = localApiToken;
  }

  async request(path, { method = "GET", body, signal } = {}) {
    const response = await fetch(this.baseUrl + path, {
      method, signal,
      headers: { Authorization: `Bearer ${this.token}`, "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.error?.message || `HTTP ${response.status}`);
    return data;
  }

  async start(conversationId, message, permittedTools = []) {
    return this.request(`/v1/conversations/${encodeURIComponent(conversationId)}/runs`, {
      method: "POST", body: { message, permitted_tools: permittedTools },
    });
  }

  async *events(runId, { after = 0, signal } = {}) {
    const response = await fetch(this.baseUrl + `/v1/runs/${encodeURIComponent(runId)}/events`, {
      headers: { Authorization: `Bearer ${this.token}`, "Last-Event-ID": String(after) }, signal,
    });
    if (!response.ok) throw new Error(`Event stream HTTP ${response.status}`);
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    try {
      while (true) {
        const { value, done } = await reader.read();
        buffer += decoder.decode(value, { stream: !done });
        buffer = buffer.replace(/\r\n/g, "\n");
        let boundary;
        while ((boundary = buffer.indexOf("\n\n")) >= 0) {
          const block = buffer.slice(0, boundary);
          buffer = buffer.slice(boundary + 2);
          const data = block.split("\n").filter(line => line.startsWith("data: "))
            .map(line => line.slice(6)).join("\n");
          if (data) yield JSON.parse(data);
        }
        if (done) break;
      }
    } finally {
      await reader.cancel().catch(() => {});
      reader.releaseLock();
    }
  }

  cancel(runId) {
    return this.request(`/v1/runs/${encodeURIComponent(runId)}/cancel`, { method: "POST" });
  }

  decide(approvalId, digest, approve) {
    return this.request(`/v1/approvals/${encodeURIComponent(approvalId)}/decision`, {
      method: "POST", body: { digest, approve },
    });
  }
}
