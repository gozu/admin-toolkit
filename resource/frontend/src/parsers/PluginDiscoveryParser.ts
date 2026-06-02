import type { ExtractedFiles } from '../types';

interface PluginDiscoveryResult {
  plugins: string[];
  pluginsCount: number;
}

export class PluginDiscoveryParser {
  private extractedFiles: ExtractedFiles;

  constructor(extractedFiles: ExtractedFiles) {
    this.extractedFiles = extractedFiles;
  }

  parse(): PluginDiscoveryResult {
    const pluginDirs = new Set<string>();

    for (const path in this.extractedFiles) {
      if (path.includes('/plugins/')) {
        // Extract plugin name from path
        const parts = path.split('/');
        const pluginsIndex = parts.indexOf('plugins');

        if (pluginsIndex >= 0 && pluginsIndex + 1 < parts.length) {
          const pluginName = parts[pluginsIndex + 1];
          if (
            pluginName &&
            pluginName.length > 0 &&
            !pluginName.includes('.')
          ) {
            pluginDirs.add(pluginName);
          }
        }
      }
    }

    // Store both the full list (sorted alphabetically) and the count
    return {
      plugins: Array.from(pluginDirs).sort(),
      pluginsCount: pluginDirs.size,
    };
  }
}
