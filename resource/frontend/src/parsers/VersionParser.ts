import { BaseJSONParser } from './BaseParser';

interface VersionResult {
  dssVersion?: string;
}

export class VersionParser extends BaseJSONParser<VersionResult> {
  processData(data: { product_version?: string }): VersionResult {
    if (data.product_version) {
      return { dssVersion: data.product_version };
    }
    return {};
  }
}
