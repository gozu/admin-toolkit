export interface SseFrame {
  event: string;
  payload: unknown;
}

export async function* parseSseStream(
  body: ReadableStream<Uint8Array>,
): AsyncGenerator<SseFrame> {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) return;
    buffer += decoder.decode(value, { stream: true });
    const parts = buffer.split('\n\n');
    buffer = parts.pop() ?? '';
    for (const part of parts) {
      const event = part.match(/^event:\s*(\S+)/m)?.[1];
      const data = part.match(/^data:\s*(.*)/m)?.[1];
      if (!event || data === undefined) continue;
      try {
        yield { event, payload: JSON.parse(data) };
      } catch {
        /* skip malformed frame */
      }
    }
  }
}
