import { BaseJSONParser } from './BaseParser';
import { formatCamelCase, formatDateString } from '../utils/formatters';
import type { LicenseProperties } from '../types';

interface LicenseData {
  content?: {
    licensee?: {
      company?: string;
    };
    properties?: Record<string, unknown>;
    expiresOn?: string;
    usage?: {
      namedUsers?: { current: number; limit: number };
      concurrentUsers?: { current: number; limit: number };
      connections?: { current: number; limit: number };
      projects?: { current: number; limit: number };
      features?: Array<{ name: string; current: number; limit: number }>;
    };
  };
}

interface LicenseResult {
  license: LicenseData;
  company: string | null;
  licenseProperties: LicenseProperties;
  hasLicenseUsage: boolean;
}

export class LicenseParser extends BaseJSONParser<LicenseResult> {
  processData(data: LicenseData): LicenseResult {
    const result: LicenseResult = {
      license: data,
      company: null,
      licenseProperties: {},
      hasLicenseUsage: false,
    };

    if (data.content && data.content.licensee) {
      result.company = data.content.licensee.company || null;
    }

    // Extract license properties
    if (data.content && data.content.properties) {
      for (const [key, value] of Object.entries(data.content.properties)) {
        const formattedKey = this.formatKey(key);
        const formattedValue = this.formatValue(key, value);
        result.licenseProperties[formattedKey] = formattedValue;
      }
    }

    // Add expiration date if present
    if (data.content && data.content.expiresOn) {
      const formattedDate = formatDateString(data.content.expiresOn);
      result.licenseProperties['Expires On'] = formattedDate;
    }

    // Extract license usage information
    if (data.content && data.content.usage) {
      this.processUsage(data.content.usage, result.licenseProperties);
      result.hasLicenseUsage = true;
    }

    return result;
  }

  private formatKey(key: string): string {
    return formatCamelCase(key, { replaceDots: true, expandAbbreviations: true });
  }

  private formatValue(key: string, value: unknown): string {
    if (key === 'emittedOn' && typeof value === 'string' && value.length === 8) {
      return formatDateString(value);
    }
    return String(value);
  }

  private processUsage(
    usage: NonNullable<LicenseData['content']>['usage'],
    licenseProperties: LicenseProperties
  ): void {
    if (!usage) return;

    // Process named users
    if (usage.namedUsers) {
      const { current, limit } = usage.namedUsers;
      licenseProperties['Named Users'] = `${current} / ${limit} (${Math.round(
        (current / limit) * 100
      )}%)`;
    }

    // Process concurrent users
    if (usage.concurrentUsers) {
      const { current, limit } = usage.concurrentUsers;
      licenseProperties['Concurrent Users'] = `${current} / ${limit} (${Math.round(
        (current / limit) * 100
      )}%)`;
    }

    // Process connections
    if (usage.connections) {
      const { current, limit } = usage.connections;
      licenseProperties['Connections'] = `${current} / ${limit} (${Math.round(
        (current / limit) * 100
      )}%)`;
    }

    // Process projects
    if (usage.projects) {
      const { current, limit } = usage.projects;
      licenseProperties['Projects'] = `${current} / ${limit} (${Math.round(
        (current / limit) * 100
      )}%)`;
    }

    // Process features
    if (usage.features) {
      for (const feature of usage.features) {
        if (
          feature.name &&
          feature.current !== undefined &&
          feature.limit !== undefined
        ) {
          const featureName = formatCamelCase(feature.name);
          const percentage =
            feature.limit > 0
              ? Math.round((feature.current / feature.limit) * 100)
              : 0;
          licenseProperties[featureName] = `${feature.current} / ${feature.limit} (${percentage}%)`;
        }
      }
    }
  }
}
