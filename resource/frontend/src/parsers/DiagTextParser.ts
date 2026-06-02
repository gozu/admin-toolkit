import { BaseTextParser } from './BaseParser';
import type { MemoryInfo, SystemLimits, FilesystemInfo } from '../types';

interface DiagTextResult {
  cpuCores: string;
  osInfo: string;
  memoryInfo: MemoryInfo;
  systemLimits: SystemLimits;
  filesystemInfo: FilesystemInfo[];
}

export class DiagTextParser extends BaseTextParser<DiagTextResult> {
  processContent(content: string): DiagTextResult {
    return {
      ...this._parseSystemInfo(content),
      ...this._parseMemoryInfo(content),
      ...this._parseSystemLimits(content),
      ...this._parseFilesystemInfo(content),
    };
  }

  private _parseSystemInfo(content: string): {
    cpuCores: string;
    osInfo: string;
  } {
    // Parse CPU info from /proc/cpuinfo
    const processors = content.match(/processor\s*:\s*\d+/g);
    const physicalIds = content.match(/physical id\s*:\s*(\d+)/g);
    const cpuCoresMatch = content.match(/cpu cores\s*:\s*(\d+)/);

    let cpuCores: string = '??';
    if (processors && cpuCoresMatch) {
      const threads = processors.length;
      const coresPerSocket = parseInt(cpuCoresMatch[1]);
      const sockets = physicalIds ? new Set(physicalIds.map(m => m.match(/(\d+)$/)?.[1])).size : 1;
      const totalCores = sockets * coresPerSocket;
      cpuCores = threads > totalCores ? `${totalCores} C / ${threads} T` : `${totalCores}`;
    }
    let osInfo = '';

    const rhMatch = content.match(
      />\s*cat\s+\/etc\/redhat-release\s*\n([^\n]+)/
    );
    const rhReleaseContents = rhMatch ? rhMatch[1].trim() : null;

    if (rhReleaseContents) {
      const noFile = rhReleaseContents.match(/No such file or directory/);
      if (noFile) {
        const debianMatch = content.match(
          />\s*lsb_release -a\s*\n.*\n.*\nDescription:\s+(.+)\n/
        );
        if (debianMatch) osInfo = debianMatch[1].trim();
      } else {
        osInfo = rhReleaseContents;
      }
    }

    const dkuDistribMatch = content.match(/DKUDISTRIB=([^\n]+)/);
    if (dkuDistribMatch) {
      osInfo += ` ${dkuDistribMatch[1]}`;
    } else {
      if (osInfo.length === 0) osInfo = 'no OS information found';
    }

    return { cpuCores, osInfo };
  }

  private _parseMemoryInfo(content: string): { memoryInfo: MemoryInfo } {
    const freeMatch = content.match(
      />\s*free\s+-m\n([\s\S]+?)(?=\n>|\n\n|$)/
    );
    if (!freeMatch || !freeMatch[1]) {
      return { memoryInfo: {} };
    }

    const memoryInfo: MemoryInfo = {};
    const lines = freeMatch[1].trim().split('\n');

    if (lines.length >= 2) {
      const headers = lines[0].trim().split(/\s+/);
      const memValues = lines[1].trim().split(/\s+/);
      const startIndex = memValues[0] === 'Mem:' ? 1 : 0;

      for (let i = 0; i < headers.length; i++) {
        const valueIndex = i + startIndex;
        if (valueIndex < memValues.length) {
          const mbValue = parseInt(memValues[valueIndex]);
          if (!isNaN(mbValue)) {
            memoryInfo[headers[i]] =
              mbValue >= 1024
                ? `${Math.round(mbValue / 1024)} GB`
                : `${mbValue.toLocaleString()} MB`;
          }
        }
      }
    }

    if (lines.length >= 3) {
      const swapValues = lines[2].trim().split(/\s+/);
      if (swapValues.length > 1 && parseInt(swapValues[1]) > 0) {
        const [swapTotal, swapUsed, swapFree] = [1, 2, 3].map((i) =>
          parseInt(swapValues[i])
        );
        const formatSwap = (v: number) =>
          v >= 1024 ? `${(v / 1024).toFixed(2)} GB` : `${v.toLocaleString()} MB`;
        memoryInfo['Swap total'] = formatSwap(swapTotal);
        memoryInfo['Swap used'] = formatSwap(swapUsed);
        memoryInfo['Swap free'] = formatSwap(swapFree);
      } else {
        memoryInfo['Swap'] = 'Not configured';
      }
    }

    const order = [
      'total',
      'used',
      'free',
      'available',
      'shared',
      'buff/cache',
      'Swap',
      'Swap total',
      'Swap used',
      'Swap free',
    ];
    const orderedMemoryInfo: MemoryInfo = {};
    for (const key of order)
      if (key in memoryInfo) orderedMemoryInfo[key] = memoryInfo[key];
    for (const key in memoryInfo)
      if (!order.includes(key)) orderedMemoryInfo[key] = memoryInfo[key];

    return { memoryInfo: orderedMemoryInfo };
  }

  private _parseSystemLimits(content: string): { systemLimits: SystemLimits } {
    const ulimitMatch = content.match(
      />\s*ulimit\s+-a\n([\s\S]+?)(?=\n>|\n\n|$)/
    );
    if (!ulimitMatch || !ulimitMatch[1]) {
      return { systemLimits: {} };
    }

    const systemLimits: SystemLimits = {};
    const lines = ulimitMatch[1].trim().split('\n');
    const priorityLimits = [
      'open files',
      'max user processes',
      'max memory size',
      'stack size',
      'max locked memory',
      'pending signals',
    ];
    const tempLimits: SystemLimits = {};

    for (const line of lines) {
      const match = line.match(/^([^(]+)\s+\(([^)]+)\)\s+(.+)$/);
      if (match) {
        const name = match[1].trim();
        const details = match[2].trim();
        let value = match[3].trim();

        if (value === 'unlimited') {
          tempLimits[name] = 'Unlimited';
          continue;
        }

        if (!isNaN(parseInt(value))) {
          const numValue = parseInt(value);
          if (details.includes('kbytes')) {
            value =
              numValue >= 1024
                ? (numValue / 1024).toFixed(2) + ' MB'
                : numValue.toLocaleString() + ' KB';
          } else if (details.includes('bytes')) {
            if (numValue >= 1024 * 1024)
              value = (numValue / (1024 * 1024)).toFixed(2) + ' MB';
            else if (numValue >= 1024)
              value = (numValue / 1024).toFixed(2) + ' KB';
            else value = numValue.toLocaleString() + ' bytes';
          } else {
            value = numValue.toLocaleString();
          }
        }
        tempLimits[name] = value;
      }
    }

    for (const limit of priorityLimits) {
      if (tempLimits[limit]) {
        systemLimits[limit] = tempLimits[limit];
        delete tempLimits[limit];
      }
    }
    Object.assign(systemLimits, tempLimits);

    return { systemLimits };
  }

  private _parseFilesystemInfo(content: string): {
    filesystemInfo: FilesystemInfo[];
  } {
    const dfMatch = content.match(/>\s*df\s+-h\n([\s\S]+?)(?=\n>|\n\n|$)/);
    if (!dfMatch || !dfMatch[1]) {
      return { filesystemInfo: [] };
    }

    const filesystemInfo: FilesystemInfo[] = [];
    const lines = dfMatch[1].trim().split('\n');

    // Skip header line
    let i = 1;
    while (i < lines.length) {
      let line = lines[i].trim();
      if (!line) {
        i++;
        continue;
      }

      // Handle wrapped lines: if next line starts with size pattern, this line is just the filesystem name
      // df -h can wrap long filesystem names to a separate line
      const parts = line.split(/\s+/);

      // Check if this looks like a continuation (no percentage pattern found)
      const hasPercentage = parts.some(p => /^\d{1,3}%$/.test(p));

      if (!hasPercentage && i + 1 < lines.length) {
        // This might be a wrapped filesystem name, combine with next line
        const nextLine = lines[i + 1].trim();
        if (nextLine) {
          line = parts[0] + ' ' + nextLine;
          i++; // Skip the next line since we merged it
        }
      }

      const finalParts = line.split(/\s+/);

      // Find the percentage column (should be pattern like "85%")
      const percentIdx = finalParts.findIndex(p => /^\d{1,3}%$/.test(p));

      if (percentIdx >= 4) {
        // Standard format: Filesystem Size Used Avail Use% Mounted
        filesystemInfo.push({
          Filesystem: finalParts.slice(0, percentIdx - 3).join(' '),
          Size: finalParts[percentIdx - 3],
          Used: finalParts[percentIdx - 2],
          Available: finalParts[percentIdx - 1],
          'Use%': finalParts[percentIdx],
          'Mounted on': finalParts.slice(percentIdx + 1).join(' '),
        });
      } else if (finalParts.length >= 6) {
        // Fallback to original logic but validate percentage
        const usePercent = finalParts[4];
        if (/^\d{1,3}%$/.test(usePercent)) {
          filesystemInfo.push({
            Filesystem: finalParts[0],
            Size: finalParts[1],
            Used: finalParts[2],
            Available: finalParts[3],
            'Use%': finalParts[4],
            'Mounted on': finalParts.slice(5).join(' '),
          });
        }
      }

      i++;
    }

    return { filesystemInfo };
  }
}
