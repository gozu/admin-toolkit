/**
 * Score-parity harness: runs the REAL frontend scoring path (JavaMemoryParser,
 * GeneralSettingsParser, calculateHealthScore) on payloads fetched by
 * score_parity.py, replicating the live-loader ParsedData assembly
 * (useApiDataLoader/phase2). Prints the HealthScore as JSON.
 *
 * Run from resource/frontend/ (so imports + node_modules resolve):
 *   npx tsx ../../scripts/agents/parity_harness.ts <payloads.json>
 */
import { readFileSync } from 'node:fs';
import { calculateHealthScore } from '../../resource/frontend/src/hooks/useHealthScore';
import { JavaMemoryParser } from '../../resource/frontend/src/parsers/JavaMemoryParser';
import { GeneralSettingsParser } from '../../resource/frontend/src/parsers/GeneralSettingsParser';
import { extractExecResourceConfigs } from '../../resource/frontend/src/utils/execResources';

const payloads = JSON.parse(readFileSync(process.argv[2], 'utf-8'));
const {
  overview, rawSettings, javaMemoryText, codeEnvs, footprint, thresholds, whitelist,
  sanity, connectionHealth, connectionUsages,
} = payloads;

// useApiDataLoader: initialData = {...overview} (+ Spark Version)
const parsedData: Record<string, unknown> = { ...overview };
if (overview.sparkVersion) {
  parsedData.sparkSettings = { 'Spark Version': overview.sparkVersion };
}

// phase2: JavaMemoryParser over /api/java-memory text
const javaParser = new JavaMemoryParser();
const javaResult = javaParser.parse(javaMemoryText || '', 'env-default.sh');
parsedData.javaMemorySettings = javaResult.javaMemorySettings || {};

// phase2: GeneralSettingsParser over /api/settings/raw
const settingsParser = new GeneralSettingsParser();
settingsParser.setExternalData({
  sparkSettings: parsedData.sparkSettings as Record<string, string> | undefined,
  memoryInfo: parsedData.memoryInfo as Record<string, string> | undefined,
  javaMemorySettings: parsedData.javaMemorySettings as Record<string, string> | undefined,
  resourceLimits: undefined,
});
const settingsResult = settingsParser.parse(JSON.stringify(rawSettings), 'general-settings.json');
parsedData.generalSettings = settingsResult.generalSettings || {};
parsedData.enabledSettings = settingsResult.enabledSettings || {};
parsedData.sparkSettings = {
  ...((parsedData.sparkSettings as object) || {}),
  ...(settingsResult.sparkSettings || {}),
};
parsedData.cgroupSettings = settingsResult.cgroupSettings || {};
parsedData.disabledFeatures = settingsResult.disabledFeatures || {};

// codeEnvs loader + footprint loader
parsedData.codeEnvs = codeEnvs.codeEnvs || [];
parsedData.projectFootprint = footprint.projects || [];
parsedData.projectFootprintSummary = footprint.summary || {};

// phase2: structured exec-config resources (shared extractor — cannot drift)
parsedData.execResourceConfigs = extractExecResourceConfigs(rawSettings);

// New inputs: JSON null (Python None) ⇒ absent ⇒ component skips, exactly
// like the Python twin's build_parsed_data.
if (sanity != null) parsedData.sanityCheck = sanity;
if (connectionHealth != null) parsedData.connectionHealth = connectionHealth;
if (connectionUsages != null) {
  parsedData.connectionDatasetUsages = connectionUsages.datasetUsages || [];
  parsedData.connectionLlmUsages = connectionUsages.llmUsages || [];
}

const score = calculateHealthScore(parsedData as never, undefined, thresholds, whitelist);
console.log(JSON.stringify({
  overall: score.overall,
  status: score.status,
  capped: score.capped,
  categories: score.categories.map((c) => ({ category: c.category, score: c.score, weight: c.weight })),
  issueIds: score.issues.map((i) => i.id),
}, null, 1));
