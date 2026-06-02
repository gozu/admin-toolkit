// Progress messages: 5 levels of escalating absurdity, 5 pairs per level.
// Every 5 messages the level increases. Each slot randomly picks one of two options.
const P: [string, string][][] = [
  [ // Level 1: Believable
    ['Tokenizing diagnostic payload...', 'Serializing input parameters...'],
    ['Validating schema constraints...', 'Checking data completeness...'],
    ['Compressing context window...', 'Optimizing token allocation...'],
    ['Cross-referencing health metrics...', 'Correlating system indicators...'],
    ['Building inference context...', 'Initializing response buffer...'],
  ],
  [ // Level 2: Plausible but embellished
    ['Normalizing multi-dimensional health indices...', 'Deduplicating redundant feature vectors...'],
    ['Calibrating confidence thresholds against baseline...', 'Applying Bayesian priors to anomaly scores...'],
    ['Resolving semantic ambiguities in plugin metadata...', 'Decomposing temporal patterns in usage telemetry...'],
    ['Running Monte Carlo simulation on failure scenarios...', 'Performing eigenvalue decomposition on correlations...'],
    ['Reticulating diagnostic splines...', 'Inverting the attention matrix...'],
  ],
  [ // Level 3: Getting weird
    ['Consulting the backup oracle database... the mystical one...', 'Politely asking the neural network to focus...'],
    ['Bribing the GPU scheduler with extra cycles...', 'Negotiating with the memory allocator for more headroom...'],
    ['Untangling the quantum state of project dependencies...', 'Apologizing to the CPU for the excessive workload...'],
    ['Performing interpretive analysis of log file auras...', 'Divining insights from the entrails of stack traces...'],
    ['Asking the model to double-check its homework...', 'Convincing the attention heads to pay attention...'],
  ],
  [ // Level 4: Quite absurd
    ['Summoning auxiliary compute sprites from the cloud realm...', 'Dispatching carrier pigeons to redundant data centers...'],
    ['Translating diagnostics into interpretive dance notation...', 'Converting health metrics to haiku for better compression...'],
    ['Consulting the ancient scrolls of deprecated documentation...', 'Channeling the spirits of legacy codebase maintainers...'],
    ['Aligning cosmic rays for optimal bit-flip prevention...', 'Calibrating the flux capacitor for temporal data analysis...'],
    ['Asking a very wise rubber duck for a second opinion...', 'Requesting peer review from a particularly opinionated squirrel...'],
  ],
  [ // Level 5: Warhammer Mechanicus tier
    ['Performing the Rite of Data Sanctification... Ave Omnissiah...', 'Applying sacred machine oil to the inference coprocessor...'],
    ['Chanting binary psalms to appease the Machine Spirit...', 'Inscribing protective hexagrammic wards around the server rack...'],
    ['The Omnissiah blesses the data-conduits... 01001111 01001101...', 'Initiating the Litany of Ignition for the cogitator arrays...'],
    ['Sacrificing a USB stick to the Forge World for faster inference...', 'A Tech-Priest has entered the datacenter to commune with the Machine God...'],
    ['The sacred data-liturgy continues... the Machine Spirit stirs...', 'By the Motive Force! The cogitator processes with divine fury...'],
  ],
];

export function getProgressMessage(index: number): string {
  const level = Math.min(Math.floor(index / 5), P.length - 1);
  const pairs = P[level];
  return pairs[index % pairs.length][Math.random() < 0.5 ? 0 : 1];
}
