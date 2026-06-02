import { BaseTextParser } from './BaseParser';
import type { JavaMemorySettings } from '../types';

interface JavaMemoryResult {
  javaMemorySettings: JavaMemorySettings;
  dssVersion: string | null;
}

export class JavaMemoryParser extends BaseTextParser<JavaMemoryResult> {
  processContent(content: string): JavaMemoryResult {
    if (!content) {
      console.error('env-default.sh content is empty');
      return {
        javaMemorySettings: {},
        dssVersion: null,
      };
    }

    const javaMemorySettings: JavaMemorySettings = {
      DKUJAVABIN: '',
      BACKEND: '',
      FEK: '',
      JEK: '',
    };

    let dssVersion: string | null = null;

    // Split content into lines
    const lines = content.split('\n');

    // Regular expressions for extracting the values
    const javaBinRegex = /^export\s+DKUJAVABIN="([^"]+)"/;
    const backendRegex = /^export\s+DKU_BACKEND_JAVA_OPTS="([^"]+)"/;
    const fekRegex = /^export\s+DKU_FEK_JAVA_OPTS="([^"]+)"/;
    const jekRegex = /^export\s+DKU_JEK_JAVA_OPTS="([^"]+)"/;
    const xmxRegex = /-Xmx(\d+[gmk])/i;
    const installDirRegex =
      /^export\s+DKUINSTALLDIR=".*dataiku-dss-([0-9.]+)"/;

    // Process each line
    for (const line of lines) {
      // Skip comments and empty lines
      if (line.trim().startsWith('#') || !line.trim()) {
        continue;
      }

      // Extract DSS version from DKUINSTALLDIR if not already set
      if (!dssVersion) {
        const installDirMatch = line.match(installDirRegex);
        if (installDirMatch && installDirMatch[1]) {
          dssVersion = installDirMatch[1];
        }
      }

      // Match DKUJAVABIN
      const javaBinMatch = line.match(javaBinRegex);
      if (javaBinMatch && javaBinMatch[1]) {
        javaMemorySettings['DKUJAVABIN'] = javaBinMatch[1];
        continue;
      }

      // Match BACKEND memory settings
      const backendMatch = line.match(backendRegex);
      if (backendMatch && backendMatch[1]) {
        const xmxMatch = backendMatch[1].match(xmxRegex);
        if (xmxMatch && xmxMatch[1]) {
          javaMemorySettings['BACKEND'] = xmxMatch[1];
        }
        continue;
      }

      // Match FEK memory settings
      const fekMatch = line.match(fekRegex);
      if (fekMatch && fekMatch[1]) {
        const xmxMatch = fekMatch[1].match(xmxRegex);
        if (xmxMatch && xmxMatch[1]) {
          javaMemorySettings['FEK'] = xmxMatch[1];
        }
        continue;
      }

      // Match JEK memory settings
      const jekMatch = line.match(jekRegex);
      if (jekMatch && jekMatch[1]) {
        const xmxMatch = jekMatch[1].match(xmxRegex);
        if (xmxMatch && xmxMatch[1]) {
          javaMemorySettings['JEK'] = xmxMatch[1];
        }
        continue;
      }
    }

    return { javaMemorySettings, dssVersion };
  }
}
