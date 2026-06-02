import { BaseJSONParser } from './BaseParser';
import type { User, UserStats } from '../types';

interface UsersData {
  users?: Array<{
    login?: string;
    email?: string;
    enabled?: boolean;
    userProfile?: string;
  }>;
  groups?: Array<unknown>;
}

interface UsersResult {
  userStats: UserStats;
  users: User[];
}

export class UsersParser extends BaseJSONParser<UsersResult> {
  processData(data: UsersData): UsersResult {
    const userStats: UserStats = {};

    if (data && data.users && Array.isArray(data.users)) {
      const allUsers = data.users;
      const enabledUsers = data.users.filter((user) => user.enabled === true);

      userStats['Total Users'] = allUsers.length;
      userStats['Enabled Users'] = enabledUsers.length;

      // Count user profiles (only enabled users)
      const profileCounts: Record<string, number> = {};
      for (const user of enabledUsers) {
        if (user && user.userProfile) {
          if (!profileCounts[user.userProfile]) {
            profileCounts[user.userProfile] = 0;
          }
          profileCounts[user.userProfile]++;
        }
      }

      // Add profile counts to stats
      Object.assign(userStats, profileCounts);

      // Count groups
      if (data.groups && Array.isArray(data.groups)) {
        userStats['Total Groups'] = data.groups.length;
      }
    }

    const users: User[] = (data.users || []).map((u) => ({
      login: u.login || '',
      email: u.email,
      enabled: u.enabled,
      userProfile: u.userProfile,
    }));

    return { userStats, users };
  }
}
