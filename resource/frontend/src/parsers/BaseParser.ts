/**
 * Base class for JSON file parsers
 */
export abstract class BaseJSONParser<T = unknown> {
  parse(content: string, filename: string): T {
    try {
      const data = JSON.parse(content);
      return this.processData(data, filename);
    } catch {
      // Try alternative parsing for malformed JSON
      const lines = content.split('\n');
      const result: Record<string, string> = {};
      for (const line of lines) {
        if (line.includes(':')) {
          const [key, ...valueParts] = line.split(':');
          const value = valueParts.join(':').trim().replace(/['"]/g, '');
          result[key.trim()] = value;
        }
      }
      return this.processData(result as unknown, filename);
    }
  }

  abstract processData(data: unknown, filename: string): T;
}

/**
 * Base class for text file parsers
 */
export abstract class BaseTextParser<T = unknown> {
  parse(content: string, filename: string): T {
    return this.processContent(content, filename);
  }

  abstract processContent(content: string, filename: string): T;
}
