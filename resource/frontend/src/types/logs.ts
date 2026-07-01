// Log error types
export interface LogError {
  timestamp: string;
  data: string[];
}

export interface LogStats {
  'Total Lines': number;
  'Unique Errors': number;
  'Displayed Errors': number;
}

// AI Log Analysis types
export interface LlmOption {
  id: string;
  label: string;
  type: string;
  connection: string;
  model: string;
}
