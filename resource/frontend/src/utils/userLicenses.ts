/**
 * Licensing profiles, not security permissions. Keep the original value for display.
 * Current and legacy names: https://doc.dataiku.com/dss/latest/security/user-profiles.html
 * Unknown/custom profiles require review; excluding consumers alone would incorrectly
 * classify technical accounts, governance users, and future profiles as designers.
 */
export type UserLicenseCategory = 'full' | 'builder' | 'consumer' | 'other' | 'unknown';
export type UserLicenseFilter = 'all' | 'designers' | UserLicenseCategory;

const PROFILE_CATEGORIES: Readonly<Record<string, UserLicenseCategory>> = {
  FULL_DESIGNER: 'full',
  DESIGNER: 'full',
  DATA_SCIENTIST: 'full',
  DATA_DESIGNER: 'builder',
  ADVANCED_ANALYTICS_DESIGNER: 'builder',
  DATA_ANALYST: 'builder',
  READER: 'consumer',
  EXPLORER: 'consumer',
  CONSUMER: 'consumer',
  AI_CONSUMER: 'consumer',
  AI_ACCESS_USER: 'consumer',
  READ_ONLY: 'consumer',
  READONLY: 'consumer',
  TECHNICAL_ACCOUNT: 'other',
  PLATFORM_ADMIN: 'other',
  GOVERNANCE_MANAGER: 'other',
};

export const USER_LICENSE_LABELS: Record<UserLicenseCategory, string> = {
  full: 'Full designer access',
  builder: 'Builder access (limited features)',
  consumer: 'Consumer / read-only',
  other: 'Technical / admin / governance',
  unknown: 'Unknown profile — review license',
};

export const USER_LICENSE_FILTERS: readonly { value: UserLicenseFilter; label: string }[] = [
  { value: 'all', label: 'All licenses' },
  { value: 'designers', label: 'Designer / builder licenses' },
  { value: 'full', label: 'Full designer access only' },
  { value: 'consumer', label: 'Consumer / read-only' },
  { value: 'other', label: 'Technical / admin / governance' },
  { value: 'unknown', label: 'Unknown profiles' },
];

export function classifyUserLicense(profile: string | null | undefined): UserLicenseCategory {
  const normalized = (profile ?? '')
    .trim()
    .toUpperCase()
    .replace(/[\s-]+/g, '_');
  return Object.hasOwn(PROFILE_CATEGORIES, normalized) ? PROFILE_CATEGORIES[normalized] : 'unknown';
}

export function matchesUserLicense(
  profile: string | null | undefined,
  filter: UserLicenseFilter,
): boolean {
  if (filter === 'all') return true;
  const category = classifyUserLicense(profile);
  return filter === 'designers'
    ? category === 'full' || category === 'builder'
    : category === filter;
}
