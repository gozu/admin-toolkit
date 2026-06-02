import { BaseTextParser } from './BaseParser';
import type { ConnectionCounts, ConnectionDetail } from '../types';

interface ConnectionsResult {
  connections: ConnectionCounts;
  connectionDetails: ConnectionDetail[];
}

interface ConnectionConfig {
  type: string;
  params?: {
    driverClassName?: string;
    [key: string]: unknown;
  };
  [key: string]: unknown;
}

type ConnectionsJSON = Record<string, ConnectionConfig>;

export class ConnectionsParser extends BaseTextParser<ConnectionsResult> {
  processContent(content: string): ConnectionsResult {
    // Try JSON parsing first
    try {
      const data = JSON.parse(content);
      const result = this.parseJSONConnections(data);

      // If we got results, return them
      if (Object.keys(result.connections).length > 0) {
        return result;
      }
    } catch {
      // JSON parsing failed, fall through to text-based parsing
    }

    // Fallback to text-based parsing (original approach)
    return this.parseTextConnections(content);
  }

  private parseJSONConnections(data: unknown): ConnectionsResult {
    const connectionTypes: ConnectionCounts = {};
    const connectionDetails: ConnectionDetail[] = [];

    const connectionsData = data as ConnectionsJSON;

    for (const [connectionName, config] of Object.entries(connectionsData)) {
      if (!config || typeof config !== 'object') continue;

      let connectionType = config.type;
      if (!connectionType) continue;

      // Replace EC2 with S3 (legacy behavior)
      if (connectionType === 'EC2') {
        connectionType = 'S3';
      }

      // For JDBC connections, append driver class name if present
      const driverClassName = config.params?.driverClassName;
      let displayType = connectionType;

      if (connectionType === 'JDBC' && driverClassName) {
        // Create composite type: "JDBC (com.driver.ClassName)"
        // Truncate very long driver names for display
        const shortDriverName = driverClassName.length > 50
          ? driverClassName.substring(0, 47) + '...'
          : driverClassName;
        displayType = `JDBC (${shortDriverName})`;
      }

      // Store connection detail
      connectionDetails.push({
        name: connectionName,
        type: connectionType,
        driverClassName: driverClassName,
      });

      // Count by display type (includes driver info for JDBC)
      if (!connectionTypes[displayType]) {
        connectionTypes[displayType] = 0;
      }
      connectionTypes[displayType]++;
    }

    return { connections: connectionTypes, connectionDetails };
  }

  private parseTextConnections(content: string): ConnectionsResult {
    const lines = content.split('\n');
    const connectionTypes: ConnectionCounts = {};

    for (const line of lines) {
      if (line.includes('"type":')) {
        const match = line.match(/"type"\s*:\s*"([^"]+)"/);
        if (match && match[1]) {
          // Replace EC2 with S3
          let connectionType = match[1];
          if (connectionType === 'EC2') {
            connectionType = 'S3';
          }

          if (!connectionTypes[connectionType]) {
            connectionTypes[connectionType] = 0;
          }
          connectionTypes[connectionType]++;
        }
      }
    }

    return { connections: connectionTypes, connectionDetails: [] };
  }
}
