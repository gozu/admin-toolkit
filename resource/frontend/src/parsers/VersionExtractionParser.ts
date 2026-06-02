import { BaseTextParser } from './BaseParser';

interface VersionExtractionResult {
  sparkVersion?: string;
  pythonVersion: string;
}

export class VersionExtractionParser extends BaseTextParser<VersionExtractionResult> {
  processContent(content: string): VersionExtractionResult {
    const result: VersionExtractionResult = {
      pythonVersion: 'Unknown',
    };

    // Parse Spark version from diag.txt
    const sparkVersionMatch = content.match(/DKU_SPARK_VERSION=([^\s\n]+)/);
    if (sparkVersionMatch && sparkVersionMatch[1]) {
      result.sparkVersion = sparkVersionMatch[1];
    }

    // Parse Python version in various formats
    const pythonMatch = content.match(
      /Python\s+([\d.]+)|python.*?version\s+([\d.]+)|>\s*\/.*?python\s+-V\n([^\n]+)/i
    );
    if (pythonMatch) {
      result.pythonVersion = (
        pythonMatch[1] ||
        pythonMatch[2] ||
        pythonMatch[3]
      ).trim();
    }

    return result;
  }
}
