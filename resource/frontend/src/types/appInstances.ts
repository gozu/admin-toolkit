/** An app template as DSS lists it (`client.list_apps()`). `useAsRecipe` is
 *  true when the app carries `useAsRecipeSettings`, i.e. it can be dropped into
 *  a flow as an `App_<appId>` recipe — the only apps whose instances accumulate
 *  from automated runs. */
export interface AppTemplateRow {
  appId: string;
  label: string;
  /** 'PROJECT' for an app converted from a project, 'PLUGIN' for one shipped by a plugin. */
  origin: string;
  originProjectKey: string | null;
  useAsRecipe: boolean;
  instanceCount: number;
  lastInstantiation: number | null;
  instanceOwners: string[];
}

/** One APP_INSTANCE project. `creator*` and `isTemporary` come from the
 *  app-instances macro and stay null when it is unavailable — DSS strips both
 *  from every public-API projection. */
export interface AppInstanceRow {
  projectKey: string;
  name: string;
  owner: string;
  generatingAppId: string | null;
  generatingAppVersion: string | null;
  lastModified: number | null;
  /** '<PROJECT_KEY>.<recipeName>' of the App recipe whose run created this.
   *  Null when the instance was created from the app's homepage instead —
   *  DSS stores the app id in that same field, so the backend only fills these
   *  when the stored value differs from `generatingAppId`. */
  creatorFullId: string | null;
  creatorProjectKey: string | null;
  creatorRecipeName: string | null;
  /** DSS's isTemporaryAppInstance: git + catalog indexing disabled. Set for
   *  recipe runs, but also for API-created temporary instances — it is not on
   *  its own a marker of recipe origin. */
  isTemporary: boolean | null;
  /** True when the creating recipe no longer exists. Null = undetermined. */
  orphan: boolean | null;
}

/** One `App_*` recipe. `keepInstance` needs a per-recipe settings fetch —
 *  `list_recipes()` omits `params` — so it is null when that fetch failed. */
export interface AppRecipeRow {
  projectKey: string;
  name: string;
  fullId: string;
  appId: string;
  keepInstance: boolean | null;
  error: string | null;
}

export interface AppInstanceAttribution {
  available: boolean;
  error?: string | null;
  attributed?: number;
  unreadable?: number;
}

export interface AppInstancesResult {
  apps: AppTemplateRow[];
  instances: AppInstanceRow[];
  recipes: AppRecipeRow[];
  attribution: AppInstanceAttribution;
  projectsToScan: number;
  projectsScanned: number;
  failedProjects: { projectKey: string; error: string }[];
  /** Null when the sweep or the macro was incomplete — never silently zero. */
  orphans: number | null;
}
