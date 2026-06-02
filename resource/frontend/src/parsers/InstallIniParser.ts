import { BaseTextParser } from './BaseParser';

interface InstallIniResult {
  nodeId?: string;
  installId?: string;
}

export class InstallIniParser extends BaseTextParser<InstallIniResult> {
  processContent(content: string): InstallIniResult {
    const result: InstallIniResult = {};
    const lines = content.split('\n');

    let inGeneralSection = false;

    for (const line of lines) {
      const trimmedLine = line.trim();

      // Check for section headers
      if (trimmedLine.startsWith('[') && trimmedLine.endsWith(']')) {
        const section = trimmedLine.slice(1, -1).toLowerCase();
        inGeneralSection = section === 'general';
        continue;
      }

      // Parse key=value pairs in [general] section
      if (inGeneralSection && trimmedLine.includes('=')) {
        const eqIndex = trimmedLine.indexOf('=');
        const key = trimmedLine.slice(0, eqIndex).trim().toLowerCase();
        const value = trimmedLine.slice(eqIndex + 1).trim();

        if (key === 'nodeid') {
          result.nodeId = value;
        } else if (key === 'installid') {
          result.installId = value;
        }
      }
    }

    return result;
  }
}
