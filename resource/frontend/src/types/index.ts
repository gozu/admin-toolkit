// Barrel re-export — every type that used to live in this file is preserved
// under `import ... from '../types'`. `export *` carries both types and the
// runtime helpers (isTerminal/isActive from lifecycle.ts).
export * from './adoption';
export * from './appInstances';
export * from './codeEnvs';
export * from './comparison';
export * from './connections';
export * from './containerExecs';
export * from './computePlacement';
export * from './core';
export * from './email';
export * from './health';
export * from './k8s';
export * from './lifecycle';
export * from './llmAudit';
export * from './logs';
export * from './plugins';
export * from './projects';
export * from './scenarios';
export * from './settings';
export * from './system';
export * from './userChurn';
