import { BaseTextParser } from './BaseParser';

interface RestartTimeResult {
  lastRestartTime: string | null;
}

export class RestartTimeParser extends BaseTextParser<RestartTimeResult> {
  processContent(content: string): RestartTimeResult {
    if (!content) {
      console.error('Supervisord log content is empty');
      return { lastRestartTime: null };
    }

    // Find the last occurrence of "backend entered RUNNING"
    const lines = content.split('\n');
    let lastRestartLine: string | null = null;

    for (let i = lines.length - 1; i >= 0; i--) {
      if (lines[i].includes('success: backend entered RUNNING state')) {
        lastRestartLine = lines[i];
        break;
      }
    }

    let lastRestartTime: string | null = null;

    if (lastRestartLine) {
      // Extract timestamp YYYY-MM-DD HH:MM:SS,SSS
      const timestampMatch = lastRestartLine.match(
        /^(\d{4}-\d{2}-\d{2}\s\d{2}:\d{2}:\d{2},\d{3})/
      );
      if (timestampMatch && timestampMatch[1]) {
        const timestampStr = timestampMatch[1];
        try {
          // Parse timestamp to a Date object (replace comma with period for milliseconds)
          const dateStr = timestampStr.replace(',', '.');
          const restartDate = new Date(dateStr);

          // Format the date nicely
          const options: Intl.DateTimeFormatOptions = {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit',
            hour12: true,
          };

          lastRestartTime = restartDate.toLocaleDateString('en-US', options);
        } catch (e) {
          console.error(`Error parsing timestamp '${timestampStr}': ${e}`);
        }
      }
    }

    return { lastRestartTime };
  }
}
