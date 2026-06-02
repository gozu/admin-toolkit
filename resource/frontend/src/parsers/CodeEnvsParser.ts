import type { CodeEnv, ExtractedFiles } from '../types';

interface CodeEnvsResult {
  codeEnvs: CodeEnv[];
  pythonVersionCounts: Record<string, number>;
  rVersionCounts: Record<string, number>;
}

interface CodeEnvData {
  pythonInterpreter?: string;
  deploymentMode?: string;
}

const SKIP_DEPLOYMENT_MODES = new Set(['PLUGIN_MANAGED', 'DSS_INTERNAL']);

export class CodeEnvsParser {
  private extractedFiles: ExtractedFiles;

  constructor(extractedFiles: ExtractedFiles) {
    this.extractedFiles = extractedFiles;
  }

  parse(): CodeEnvsResult {
    const codeEnvs: CodeEnv[] = [];
    const pythonVersionCounts: Record<string, number> = {};
    const rVersionCounts: Record<string, number> = {};

    for (const path in this.extractedFiles) {
      if (path.includes('/code-envs/desc/') && path.endsWith('desc.json')) {
        try {
          const content = this.extractedFiles[path];
          const data: CodeEnvData = JSON.parse(content);

          // Skip plugin-managed and DSS-internal code envs
          if (data.deploymentMode && SKIP_DEPLOYMENT_MODES.has(data.deploymentMode)) {
            continue;
          }

          const parts = path.split('/');
          const envName = parts[parts.length - 2];

          // Detect language: Python envs have pythonInterpreter, R envs don't
          if (data.pythonInterpreter) {
            // Python environment
            let pythonVersion = 'NA';
            const verString = data.pythonInterpreter.replace('PYTHON', '');

            if (verString.length > 0) {
              const majorVersion = verString[0];
              const minorVersion = verString.substring(1);
              pythonVersion = majorVersion + '.' + minorVersion;
            }

            codeEnvs.push({
              name: envName,
              version: pythonVersion,
              language: 'python',
            });

            if (!pythonVersionCounts[pythonVersion]) {
              pythonVersionCounts[pythonVersion] = 0;
            }
            pythonVersionCounts[pythonVersion]++;
          } else {
            // R environment (no pythonInterpreter field)
            const rVersion = 'R';

            codeEnvs.push({
              name: envName,
              version: rVersion,
              language: 'r',
            });

            if (!rVersionCounts[rVersion]) {
              rVersionCounts[rVersion] = 0;
            }
            rVersionCounts[rVersion]++;
          }
        } catch (error) {
          console.error('Error parsing code env:', error);
        }
      }
    }

    return { codeEnvs, pythonVersionCounts, rVersionCounts };
  }
}
