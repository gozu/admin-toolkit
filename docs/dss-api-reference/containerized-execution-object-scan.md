# Containerized Execution Object Scan

This note documents where DSS stores containerized execution selections on project
objects, based on official DSS documentation and API probes against the AKAOS DSS
instance.

## Sources

- Dataiku DSS container concepts: https://doc.dataiku.com/dss/latest/containers/concepts.html
- Containerized visual recipes: https://doc.dataiku.com/dss/latest/containers/containerized-dss-engine.html
- Containerized notebooks: https://doc.dataiku.com/dss/latest/notebooks/containerized-notebooks.html
- Code Studios concepts: https://doc.dataiku.com/dss/latest/code-studios/concepts.html
- API Node / API Deployer concepts: https://doc.dataiku.com/dss/latest/apinode/concepts.html
- DSS REST API: https://doc.dataiku.com/dss/api/latest/rest/
- DSS Python project API: https://developer.dataiku.com/latest/api-reference/python/projects.html

## Core Finding

There is no single project-level DSS API response that enumerates every project
object using a container execution config. `GET /projects/{projectKey}/settings`
only exposes project defaults and bundle-remapping metadata. To find actual
object-level container selections, the scanner must enumerate each supported
object type and inspect that object's raw settings.

Global execution configs come from general settings:

```text
GET /admin/general-settings
containerSettings.executionConfigs[]
```

On AKAOS, the current config names are `eks-default` and `eks-gpu`, both
Kubernetes configs. `containerSettings.defaultExecutionConfig` is currently not
set.

## Object-Level Fields

These are the fields the scanner must parse when present.

| Surface | API read | Raw path | Notes |
| --- | --- | --- | --- |
| Project default for code workloads | `project.get_settings().get_raw()` | `settings.container` | Applies as a default to code workloads that inherit. Explicit configs use `containerMode: EXPLICIT_CONTAINER` plus `containerConf`. |
| Project default for visual recipes | `project.get_settings().get_raw()` | `settings.containerForVisualRecipesWorkloads` | Separate from the code-workload default. Do not collapse it into `settings.container`. |
| Project default for webapp backends | `project.get_settings().get_raw()` | `settings.virtualWebAppBackendSettings.infra.containerSelection` | Webapps also have their own per-object override. |
| Python/R code recipes | `GET /projects/{projectKey}/recipes/{recipeName}` | `recipe.params.containerSelection` | Verified on Python and R fixtures. Do not generalize this to every code recipe subtype. |
| Visual recipes | `GET /projects/{projectKey}/recipes/{recipeName}` | `recipe.params.engineParams.containerSelection` | Observed on `shaker` and `sync`; only meaningful for DSS-engine visual recipes. |
| Webapps | `GET /projects/{projectKey}/webapps/{webappId}` | `params.infra.containerSelection` | Present on standard and plugin webapps. Raw webapp settings can contain `apiKey`; redact sensitive fields in logs. |
| ML tasks | `GET /projects/{projectKey}/models/lab/{analysisId}/{mlTaskId}/settings` | `containerSelection` | The lab list endpoint is `GET /projects/{projectKey}/models/lab/`; it returns `mlTasks[]` with `analysisId` and `mlTaskId`. |
| Code Studio templates | `GET /admin/code-studios/{templateId}` | `defaultContainerConf`, `containerConfs`, `allContainerConfs`, `allowContainerConfOverride` | This is an admin/global template surface, not a project object. Project Code Studio objects normally reference `templateId`. |
| Code Studio objects | `GET /projects/{projectKey}/code-studios/{id}` | no direct container selection observed | Use the object `templateId` to resolve the template config. |
| Bundle remapping | `project.get_settings().get_raw()` | `bundleContainerSettings.remapping.containerExecs` | Only appears on some projects and can be empty unless bundle remapping is configured. |

## Tested Non-Carriers

These surfaces are easy to over-assume and should be scanned or documented as
non-carriers unless fixture testing proves otherwise.

| Surface | Observed raw field | Current interpretation |
| --- | --- | --- |
| Jupyter notebooks | `kernelSpec` on list items; `metadata.kernelspec` in notebook content | Notebook JSON records the selected kernel, not a `containerSelection` object. Containerized notebook use may require interpreting kernel specs or running-session metadata. |
| SQL notebooks | no container field observed | SQL execution is connection/engine driven, not a per-notebook container execution config. |
| Scenarios | `params.envSelection` for custom Python and Python steps | Scenarios expose code-env selection, but no per-scenario container selection was observed. |
| PySpark, Spark Scala, Spark SQL recipes | Spark config / execution-engine fields | The fixture API accepted the recipe objects but DSS did not retain `params.containerSelection`; treat these as Spark/cluster execution surfaces, not container-exec selectors. |
| Shell recipes | no container field observed | The fixture shell recipe did not retain `params.containerSelection`. |
| API services in API Designer | endpoint `envSelection` only on a Python function endpoint | API Node Kubernetes deployment is an API Deployer / infrastructure concern, not a project API service settings field. |
| Model evaluation stores and model comparisons | no container field observed | Model evaluation stores include Spark metric-engine settings, but no container-exec selection. Model comparisons had no container-like field. |
| Saved models | fixture creation failed on AKAOS | AKAOS returned a DSS sudo/process error for saved-model creation; no conclusion beyond the baseline absence of saved models. Saved models should not be treated as ML task settings. |

## Parsing Rules

- Treat `INHERIT`, `NONE`, and `EXPLICIT_CONTAINER` as distinct states.
- The Admin Toolkit feature reports only meaningful explicit overrides:
  - Project rows require `EXPLICIT_CONTAINER` plus a concrete `containerConf`
    that differs from `containerSettings.defaultExecutionConfig`.
  - Job/object rows require `EXPLICIT_CONTAINER` plus a concrete
    `containerConf` that differs from both the instance default and that
    object's project-level baseline.
  - Inherited rows and explicit rows equal to the inherited baseline are omitted
    because they are handled by replacing the instance-level or project-level
    default container execution config.
- Resolve inherited values by workload family:
  - Python/R code recipe/code workload: object `params.containerSelection` -> project `settings.container` -> global `containerSettings.defaultExecutionConfig`
  - visual recipe: object `params.engineParams.containerSelection` -> project `settings.containerForVisualRecipesWorkloads` -> global default
  - webapp backend: object `params.infra.containerSelection` -> project `settings.virtualWebAppBackendSettings.infra.containerSelection` -> global webapp/default settings
  - ML task: object `containerSelection` -> project/default ML/container setting, if DSS applies inheritance
- Keep Spark/Kubernetes cluster selection separate from container execution config. Paths such as `settings.k8sCluster`, `settings.cluster`, and `sparkParams.sparkExecutionEngine` are related compute settings, not container execution config names.
- Redact keys matching `key`, `secret`, `password`, `token`, or `credential` when logging raw object settings.
- Use full-object GET followed by a narrow in-memory patch and full-object PUT/POST when replacing settings. The Dataiku Python client exposes saves this way for project, recipe, webapp, scenario, API service, ML task, and Code Studio template settings.

## AKAOS Baseline Before Fixture Creation

The first AKAOS scan covered 10 projects and found:

- Project settings in all projects with project-level container defaults.
- 29 Python recipes with `params.containerSelection.containerMode`.
- 7 visual recipes (`shaker`/`sync`) with `params.engineParams.containerSelection.containerMode`.
- 32 webapps with `params.infra.containerSelection.containerMode`.
- 1 clustering ML task with top-level `containerSelection.containerMode`.
- 6 Code Studio objects referencing templates, plus one Code Studio template with `defaultContainerConf: eks-default`.
- 16 Jupyter notebooks with kernel specs but no `containerSelection`.
- 2 SQL notebooks with no container field.
- 9 scenarios with code-env selection but no container field.
- 0 API services, 0 saved models, 0 model evaluation stores, and 0 model comparisons.

Most object-level selections were `INHERIT`; one standard webapp had
`containerMode: NONE`. Explicit fixture objects are required to verify scanner
output for `EXPLICIT_CONTAINER`.

## Fixture Round

Two dedicated AKAOS projects were created for the second scan:

- `CDE_SCAN_FIX_A`, using `eks-default`
- `CDE_SCAN_FIX_B`, using `eks-gpu`

Each fixture project has these objects:

- project-level explicit container defaults for code workloads, visual recipes,
  and webapp backends
- non-empty `bundleContainerSettings.remapping.containerExecs`
- Python and R code recipes with explicit `params.containerSelection`
- PySpark, Spark Scala, Spark SQL, and shell recipes to prove they are not
  parsed as Python/R CDE carriers
- `shaker` and `sync` visual recipes with explicit
  `params.engineParams.containerSelection`
- a standard webapp with explicit `params.infra.containerSelection`
- custom-Python and step-based scenarios
- Jupyter and SQL notebooks
- a Code Studio object using template `tst`
- an API service with a Python function endpoint
- one ML task per project, prediction in A and clustering in B
- model evaluation store and model comparison objects

The second scan verified these explicit values:

```text
CDE_SCAN_FIX_A project settings:
  settings.container.containerConf = eks-default
  settings.containerForVisualRecipesWorkloads.containerConf = eks-default
  settings.virtualWebAppBackendSettings.infra.containerSelection.containerConf = eks-default
  bundleContainerSettings.remapping.containerExecs[0].target = eks-default

CDE_SCAN_FIX_B project settings:
  settings.container.containerConf = eks-gpu
  settings.containerForVisualRecipesWorkloads.containerConf = eks-gpu
  settings.virtualWebAppBackendSettings.infra.containerSelection.containerConf = eks-gpu
  bundleContainerSettings.remapping.containerExecs[0].target = eks-gpu
```

For object-level fixture fields:

| Fixture object | A result | B result | Scanner action |
| --- | --- | --- | --- |
| `cde_scan_python` | `recipe.params.containerSelection.containerConf = eks-default` | `eks-gpu` | Include as Python/R code recipe carrier. |
| `cde_scan_r` | `recipe.params.containerSelection.containerConf = eks-default` | `eks-gpu` | Include as Python/R code recipe carrier. |
| `cde_scan_prepare` | `recipe.params.engineParams.containerSelection.containerConf = eks-default` | `eks-gpu` | Include as visual recipe carrier. |
| `cde_scan_sync` | `recipe.params.engineParams.containerSelection.containerConf = eks-default` | `eks-gpu` | Include as visual recipe carrier. |
| `cde_scan_webapp` | `params.infra.containerSelection.containerConf = eks-default` | `eks-gpu` | Include as webapp carrier. |
| `cde_scan_pyspark` | no `containerSelection` retained | no `containerSelection` retained | Exclude from CDE object parser; track Spark config separately if needed. |
| `cde_scan_spark_scala` | no `containerSelection` retained | no `containerSelection` retained | Exclude from CDE object parser; track Spark config separately if needed. |
| `cde_scan_spark_sql_query` | no `containerSelection` retained | no `containerSelection` retained | Exclude from CDE object parser; track Spark config separately if needed. |
| `cde_scan_shell` | no `containerSelection` retained | no `containerSelection` retained | Exclude as own CDE carrier. |
| ML task | `containerSelection.containerMode = INHERIT` | `INHERIT` | Include the ML task field in scans. Explicit patch could not be saved because AKAOS returned a DSS internal preprocessing/null error after ML guessing failed. |
| Code Studio object | `templateId = tst` only | `templateId = tst` only | Resolve to template. |
| Code Studio template `tst` | `defaultContainerConf = eks-default`, `allContainerConfs = true`, `allowContainerConfOverride = true` | global template | Include template scan as admin/global surface. |
| API service Python function endpoint | `envSelection`, no `containerSelection` | same | Do not classify project API service settings as CDE usage. |
| Custom/step scenarios | `envSelection`, no `containerSelection` | same | Do not classify scenarios as CDE usage. |
| Jupyter notebook | `kernelSpec.name = python3` | same | Record kernel only; no object-level container config in notebook JSON. |
| SQL notebook | connection only, no container field | same | Exclude from CDE object parser. |
| Model evaluation store | Spark metric-engine flag only | same | Exclude from CDE object parser. |
| Model comparison | no container-like field | same | Exclude from CDE object parser. |

## Scanner Shape

A container execution scan should return at least:

- global container configs from `containerSettings.executionConfigs[]`
- the instance default from `containerSettings.defaultExecutionConfig`
- explicit per-project defaults from project settings only when they differ from
  the instance default
- explicit object overrides from Python/R recipes, visual recipes, webapps, and
  ML tasks only when they differ from both the instance default and project
  baseline
- bundle remapping references separately from runtime usages
- tested non-carrier rows or debug counters for notebooks, scenarios, API services,
  Spark recipes, model evaluation stores, and model comparisons so false
  assumptions stay visible

The scanner should not report Spark execution engine, Kubernetes cluster, SQL
connection, code-env-only, or notebook kernel fields as container execution
configs unless later DSS versions introduce an explicit container selection field
on those objects.

## Admin Toolkit Implementation

The feature lives under **Compute Fabric -> Container Execs**. Docker image
cleanup also moved under Compute Fabric, because both features relate to external
compute infrastructure rather than code environment package inventory.

Backend routes:

- `GET /api/container-execs`
  - Scans global container execution configs, all project settings, supported
    project objects, and verified non-carrier counts.
  - Returns `usageRows` and grouped `projectRows` only for explicit project-level
    or project-object-level overrides that differ from the relevant default.
    Projects with only inherited/default container behavior do not appear.
  - Returns `globalDefaultConfig` so the UI can show the active instance default
    without treating it as replaceable usage.
  - Supports optional `projectKeys=A,B` query filtering.
  - Uses cache key `container_execs` or `container_execs:{sha1(projectKeys)}`.
  - Honors backend setting `container_exec_timeout_ms`; partial scans return
    `timedOut: true` and include a timeout event.
- `POST /api/container-execs/replace`
  - Body: `sourceConfig`, `targetConfig`, optional `dryRun`, optional
    `projectKeys`, optional `objectTypes`.
  - Replaces only explicit `EXPLICIT_CONTAINER` rows whose
    `containerConf === sourceConfig` and whose scanner row marks
    `replacementSupported: true`.
  - Dry run returns `status: planned`; apply returns per-row `updated` or
    `failed` statuses and clears the `container_execs` cache.

Replacement is intentionally narrow. Inherited rows are not emitted by the API
or UI; changing inherited behavior should be done by replacing the instance-level
default container execution config. Informational non-carrier counters are kept
only to make verified gaps visible, but they are not replacement candidates.
