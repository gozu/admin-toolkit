import { BaseTextParser } from './BaseParser';
import type { LogError, LogStats } from '../types';

function escapeHtml(str: string): string {
  return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

interface LogParserResult {
  formattedLogErrors: string;
  rawLogErrors: LogError[];
  logStats: LogStats;
}

export class LogParser extends BaseTextParser<LogParserResult> {
  processContent(content: string): LogParserResult {
    if (!content) {
      console.error('Backend log content is empty');
      return {
        formattedLogErrors: 'No log errors found',
        rawLogErrors: [],
        logStats: {
          'Total Lines': 0,
          'Unique Errors': 0,
          'Displayed Errors': 0,
        },
      };
    }

    // Configuration parameters — read from localStorage thresholds if available
    let LINES_BEFORE = 10;
    let LINES_AFTER = 100;
    let TIME_THRESHOLD_SECONDS = 5;
    let MAX_ERRORS = 5;
    try {
      const raw = window.localStorage.getItem('diagparser.thresholds');
      if (raw) {
        const t = JSON.parse(raw);
        if (typeof t.logLinesBefore === 'number') LINES_BEFORE = t.logLinesBefore;
        if (typeof t.logLinesAfter === 'number') LINES_AFTER = t.logLinesAfter;
        if (typeof t.logTimeThresholdSec === 'number') TIME_THRESHOLD_SECONDS = t.logTimeThresholdSec;
        if (typeof t.logMaxErrors === 'number') MAX_ERRORS = t.logMaxErrors;
      }
    } catch { /* use defaults */ }
    const LOG_LEVELS = ['\\[ERROR\\]', '\\[FATAL\\]', '\\[SEVERE\\]', '\\[WARN\\]'];

    // Create regex to match log levels
    const logLevelRegex = new RegExp(`(${LOG_LEVELS.join('|')})`);
    const timestampRegex = /\[(\d{4}\/\d{2}\/\d{2}-\d{2}:\d{2}:\d{2}\.\d{3})\]/;

    // Helper function to parse timestamp to seconds
    const parseTimestamp = (line: string): number | null => {
      const match = timestampRegex.exec(line);
      if (!match) return null;

      const timestampStr = match[1];
      try {
        const year = parseInt(timestampStr.substring(0, 4));
        const month = parseInt(timestampStr.substring(5, 7)) - 1;
        const day = parseInt(timestampStr.substring(8, 10));
        const hour = parseInt(timestampStr.substring(11, 13));
        const minute = parseInt(timestampStr.substring(14, 16));
        const second = parseInt(timestampStr.substring(17, 19));
        const millisecond = parseInt(timestampStr.substring(20, 23));

        const dt = new Date(year, month, day, hour, minute, second, millisecond);
        return dt.getTime() / 1000;
      } catch (e) {
        console.error(`Error parsing timestamp '${timestampStr}': ${e}`);
        return null;
      }
    };

    // Split content into lines
    const lines = content.split('\n');
    let lineCount = 0;
    let errorCount = 0;
    const recentErrors: LogError[] = [];
    const errorSignatures = new Set<string>();
    const beforeBuffer: string[] = [];
    let collectingAfter = 0;
    let afterBuffer: string[] = [];
    let currentErrorData: string[] = [];
    let lastErrorTimestamp: number | null = null;
    let errorLine = 0;
    let errorTimestampStr = '';

    // Process each line
    for (const line of lines) {
      lineCount++;

      if (collectingAfter > 0) {
        afterBuffer.push(line);
        collectingAfter--;

        if (collectingAfter === 0) {
          const equalsSigns = '='.repeat(40);
          const errorHeader = `\n${equalsSigns}\nERROR FOUND AT LINE ${errorLine} (TIMESTAMP: ${errorTimestampStr}):\n${equalsSigns}\n\n\n\n`;
          currentErrorData = [errorHeader, ...beforeBuffer, ...afterBuffer];

          recentErrors.push({
            timestamp: errorTimestampStr,
            data: currentErrorData,
          });

          if (recentErrors.length > MAX_ERRORS) {
            recentErrors.shift();
          }

          afterBuffer = [];
          currentErrorData = [];
          beforeBuffer.length = 0;
          continue;
        }
      }

      beforeBuffer.push(line);
      if (beforeBuffer.length > LINES_BEFORE) {
        beforeBuffer.shift();
      }

      const isError = logLevelRegex.test(line);
      if (isError) {
        const currentTimestamp = parseTimestamp(line);

        if (currentTimestamp === null) {
          continue;
        }

        const date = new Date(currentTimestamp * 1000);
        const timestampStr = date
          .toISOString()
          .replace('T', '-')
          .substring(0, 19);
        const errorSignature =
          line.length > 60 ? line.slice(-60).trim() : line.trim();

        if (errorSignatures.has(errorSignature)) {
          errorSignatures.delete(errorSignature);
        }

        if (lastErrorTimestamp !== null) {
          const timeDiff = currentTimestamp - lastErrorTimestamp;
          if (timeDiff < TIME_THRESHOLD_SECONDS) {
            if (collectingAfter > 0) {
              collectingAfter = Math.max(collectingAfter, LINES_AFTER);
              afterBuffer.push(line);
              collectingAfter--;
            }
            continue;
          }
        }

        errorCount++;
        errorLine = lineCount;
        errorTimestampStr = timestampStr;
        lastErrorTimestamp = currentTimestamp;
        errorSignatures.add(errorSignature);

        collectingAfter = LINES_AFTER;
        afterBuffer = [line];
        collectingAfter--;
      }
    }

    if (collectingAfter > 0) {
      const equalsSigns = '='.repeat(40);
      const errorHeader = `\n${equalsSigns}\nERROR FOUND AT LINE ${errorLine} (TIMESTAMP: ${errorTimestampStr}):\n${equalsSigns}\n\n\n\n`;
      currentErrorData = [errorHeader, ...beforeBuffer, ...afterBuffer];

      recentErrors.push({
        timestamp: errorTimestampStr,
        data: currentErrorData,
      });

      if (recentErrors.length > MAX_ERRORS) {
        recentErrors.shift();
      }
    }

    return {
      formattedLogErrors: this.formatLogErrors(recentErrors),
      rawLogErrors: recentErrors,
      logStats: {
        'Total Lines': lineCount,
        'Unique Errors': errorCount,
        'Displayed Errors': recentErrors.length,
      },
    };
  }

  private formatLogErrors(errorData: LogError[]): string {
    if (!errorData || errorData.length === 0) {
      return 'No log errors found';
    }

    let formattedOutput = '';

    for (const error of errorData) {
      formattedOutput += `<div class="log-error-block">`;

      for (const line of error.data) {
        if (line.includes('ERROR FOUND AT LINE')) {
          const modifiedLine = line.replace(/={40,}/g, '='.repeat(20));
          const headerParts = modifiedLine.split('\n');
          let formattedHeader = '';

          for (let i = 0; i < headerParts.length; i++) {
            if (headerParts[i].trim() === '') {
              formattedHeader += '<br>';
            } else {
              formattedHeader += escapeHtml(headerParts[i]) + '<br>';
            }
          }

          formattedHeader += '<br>';
          formattedOutput += `<div class="log-entry log-header">${formattedHeader}</div>`;
          continue;
        }

        let className = 'log-entry';
        if (line.includes('[INFO]')) className += ' log-info';
        else if (line.includes('[WARN]')) className += ' log-warn';
        else if (line.includes('[ERROR]')) className += ' log-error';
        else if (line.includes('[FATAL]')) className += ' log-fatal';
        else if (line.includes('[SEVERE]')) className += ' log-severe';
        else if (line.includes('[DEBUG]')) className += ' log-debug';
        else if (line.includes('[TRACE]')) className += ' log-trace';

        let formattedLine = escapeHtml(line);
        const timestampMatch = line.match(
          /\[(\d{4}\/\d{2}\/\d{2}-\d{2}:\d{2}:\d{2}\.\d{3})\]/
        );
        if (timestampMatch) {
          formattedLine = line.replace(
            timestampMatch[0],
            `<span class="log-timestamp">${timestampMatch[0]}</span>`
          );
        }

        const logLevelMatch = formattedLine.match(
          /\[(INFO|WARN|ERROR|FATAL|SEVERE|DEBUG|TRACE)\]/
        );
        if (logLevelMatch) {
          formattedLine = formattedLine.replace(
            logLevelMatch[0],
            `<span class="log-level">${logLevelMatch[0]}</span>`
          );
        }

        // Simple syntax highlighting
        formattedLine = formattedLine.replace(
          /\b(?:\d{1,3}\.){3}\d{1,3}\b/g,
          '<span class="hljs-number">$&</span>'
        );
        formattedLine = formattedLine.replace(
          /\[ct: \d+\]/g,
          '<span class="hljs-number">$&</span>'
        );
        formattedLine = formattedLine.replace(
          /\d+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com\/[a-z0-9./-]+:[a-z0-9./-]+/g,
          '<span class="hljs-string">$&</span>'
        );
        formattedLine = formattedLine.replace(
          /\b(pod|deployment|service|node|configmap|secret|namespace|replicaset|daemonset)s?\b/gi,
          '<span class="hljs-title">$&</span>'
        );
        formattedLine = formattedLine.replace(
          /Process [a-z]+ done \(return code \d+\)|Running [a-z]+ \([^)]+\)/g,
          '<span class="hljs-comment">$&</span>'
        );

        formattedOutput += `<div class="${className}">${formattedLine}</div>`;
      }

      formattedOutput += `</div>`;
    }

    return formattedOutput;
  }
}
