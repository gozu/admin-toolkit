#!/usr/bin/env python3
"""
populate_dummy_instance.py — make any DSS instance *look* like a large, busy,
multi-user deployment for the Admin Toolkit scan, using only dummy objects.

It creates DSS objects whose NUMBER and BASIC NATURE mirror a reference profile
(by default the "tam-global" profile captured from a diagnostics export), but
whose CONTENT is empty/tiny. Names and emails are synthetic ("dummy" everywhere)
so nothing real leaks.

What it creates (all cheap metadata unless noted):
  - groups
  - users            (profile mix + a few disabled, matching the profile)
  - connections      (working Filesystem substrate + dummy typed connections:
                      Snowflake / S3 / OpenAI / ... for authentic variety)
  - projects         (empty, owners spread across the dummy users)
  - datasets         (Filesystem datasets on the working connections; skewed
                      per-project distribution matching the profile)
  - managed folders  (Filesystem)
  - saved models     (empty MLflow-pyfunc containers, prediction-type mix)
  - code envs        (minimal Python/R envs — no packages; ~4s build each)

NOT faked (and why): installed plugins and code studios can't be created without
real plugin/template content; the script reports the shortfall instead of lying.

Design contract:
  * IDEMPOTENT — deterministic names (`<prefix>...####`); every stage lists what
    already exists and creates only the delta. Re-running converges; interrupting
    and re-running resumes.
  * REVERSIBLE — `--purge` deletes every object carrying the prefix.
  * SAFE-BY-DEFAULT — refuses to touch anything not carrying the prefix; per-object
    failures are logged and skipped, never abort the run.
  * GENERIC — works against any DSS instance; profile is data, overridable via
    `--profile file.json` and scalable via `--scale`.

Usage:
  python scripts/populate_dummy_instance.py --url https://host --api-key KEY
  python scripts/populate_dummy_instance.py            # reads .dss-url/.dss-api-key or $DSS_URL/$DSS_API_KEY
  python scripts/populate_dummy_instance.py --scale 0.1 --dry-run
  python scripts/populate_dummy_instance.py --only groups,users,projects
  python scripts/populate_dummy_instance.py --purge --dry-run  # preview the teardown
  python scripts/populate_dummy_instance.py --purge            # delete ONLY prefixed objects (asks first)
  python scripts/populate_dummy_instance.py --delete --yes     # same, no confirmation prompt
"""
from __future__ import annotations
import argparse, concurrent.futures as cf, math, os, sys, threading, time
from collections import Counter

try:
    import dataikuapi
except ImportError:
    sys.exit("dataikuapi not importable — run with the repo .venv:\n"
             "  ./.venv/bin/python scripts/populate_dummy_instance.py ...")

# --------------------------------------------------------------------------- #
# Reference profile — captured from the tam-global diagnostics export.
# Counts only; content is never copied. Override with --profile <file.json>.
# --------------------------------------------------------------------------- #
DEFAULT_PROFILE = {
    "source": "tam-global (diag export 2026-07-07, appVersion 0.4.671, DSS 14.7.0)",
    "projects": 451,
    "users_total": 78,
    "users_enabled": 73,
    # profile -> count (sums to enabled=73); the 5 remaining are created disabled
    "user_profiles": {
        "FULL_DESIGNER": 61, "TECHNICAL_ACCOUNT": 4, "AI_CONSUMER": 3,
        "DATA_DESIGNER": 3, "ADVANCED_ANALYTICS_DESIGNER": 1, "READER": 1,
    },
    "groups": 19,
    # DSS connection type -> count (100 total). Filesystem here is *extra* dummy
    # connections; the working dataset substrate is created separately.
    "connections_by_type": {
        "S3": 23, "Snowflake": 13, "CustomLLM": 9, "OpenAI": 7, "Filesystem": 7,
        "PostgreSQL": 7, "AzureOpenAI": 6, "SharePointOnline": 3, "Databricks": 3,
        "Azure": 2, "AzureAISearch": 2, "GCS": 2, "JDBC": 2, "SnowflakeCortex": 2,
        "Teradata": 2, "VertexAILLM": 2, "Anthropic": 1, "Bedrock": 1,
        "ElasticSearch": 1, "HuggingFaceLocal": 1, "Pinecone": 1, "Redshift": 1,
        "RemoteMCP": 1, "SQLServer": 1,
    },
    # code envs: python by version + R count (168 total = 159 py + 9 R)
    "code_envs_python_by_version": {
        "3.9": 82, "3.11": 23, "3.6": 16, "3.10": 16,
        "3.12": 7, "3.8": 6, "3.13": 4, "3.7": 4, "3.14": 1,
    },
    "code_envs_r": 9,
    "datasets": 8779,
    "managed_folders": 229,
    "saved_models_by_type": {
        "Unknown": 110, "Regression": 64, "Binary classification": 31,
        "Clustering": 8, "Multiclass": 5, "Time series forecast": 1,
    },
    # Reported-but-not-faked (need real content):
    "plugins": 137, "code_studios": 73,
}

# profile bucket -> MLflow pyfunc prediction_type (None => "Unknown")
MODEL_TYPE_TO_PRED = {
    "Regression": "REGRESSION",
    "Binary classification": "BINARY_CLASSIFICATION",
    "Multiclass": "MULTICLASS",
    "Clustering": "OTHER",
    "Time series forecast": "OTHER",
    "Unknown": None,
}
PY_VERSION_TO_INTERP = {v: "PYTHON" + v.replace(".", "") for v in
                        ["3.6", "3.7", "3.8", "3.9", "3.10", "3.11", "3.12", "3.13", "3.14"]}
# candidate fallback interpreters, most-preferred first (used when a desired one
# is not installed on the target host)
INTERP_FALLBACKS = ["PYTHON312", "PYTHON311", "PYTHON310", "PYTHON39", "PYTHON313", "PYTHON38"]

# Some DSS versions NPE on API-creating a plain "S3" connection but accept the
# S3-compatible "EC2" family type — map it so the object-store variety survives.
# (The scan will label these "EC2" rather than "S3"; see script header caveat.)
CONN_TYPE_FALLBACK = {"S3": "EC2"}

# Minimal, deliberately-nonfunctional params per connection type. Values below are
# LIVE-VERIFIED to be accepted by DSS 14.7 create_connection. Anything a given
# instance still rejects is logged and skipped — we keep whatever variety succeeds.
def conn_params(ctype: str) -> dict:
    d = "dummy.invalid"
    if ctype == "Filesystem":       return {"root": "/tmp/dummy_fs"}
    if ctype == "PostgreSQL":       return {"host": d, "port": "5432", "db": "dummy", "user": "u", "password": "p"}
    if ctype == "Snowflake":        return {"host": f"{d}.snowflakecomputing.com", "db": "DUMMY", "warehouse": "WH", "user": "u", "password": "p"}
    if ctype == "SnowflakeCortex":  return {"host": f"{d}.snowflakecomputing.com", "db": "DUMMY", "warehouse": "WH", "user": "u", "password": "p"}
    if ctype == "Redshift":         return {"host": d, "port": "5439", "db": "dummy", "user": "u", "password": "p"}
    if ctype == "SQLServer":        return {"host": d, "port": "1433", "db": "dummy", "user": "u", "password": "p"}
    if ctype == "Teradata":         return {"host": d, "db": "dummy", "user": "u", "password": "p"}
    if ctype == "Databricks":       return {"host": d, "httpPath": "/sql/1.0/dummy", "personalAccessToken": "x"}
    if ctype == "JDBC":             return {"url": "jdbc:dummy://" + d, "driverClass": "x.Driver", "user": "u", "password": "p"}
    # object stores need a defaultManagedPath + credentials block, else server NPEs
    if ctype in ("S3", "EC2"):      return {"defaultManagedPath": "/dummy", "chbucket": "dummy", "chroot": "/dummy", "credentialsMode": "KEYPAIR", "accessKey": "AKIADUMMY", "secretKey": "x"}
    if ctype == "GCS":              return {"defaultManagedPath": "/dummy", "bucket": "dummy", "credentialsMode": "KEYPAIR", "credentials": "{}"}
    if ctype == "Azure":            return {"defaultManagedPath": "/dummy", "storageAccount": "dummy", "credentialsMode": "KEYPAIR", "accessKey": "x"}
    if ctype == "OpenAI":           return {"apiKey": "sk-dummy"}
    if ctype == "AzureOpenAI":      return {"apiKey": "dummy", "resourceName": "dummy"}
    if ctype == "Anthropic":        return {"apiKey": "sk-ant-dummy"}
    if ctype == "Bedrock":          return {"region": "us-east-1", "credentialsMode": "ENVIRONMENT"}
    if ctype == "VertexAILLM":      return {"project": "dummy", "region": "us-central1"}
    if ctype == "HuggingFaceLocal": return {}
    if ctype == "CustomLLM":        return {}
    if ctype == "AzureAISearch":    return {"endpoint": "https://" + d, "apiKey": "x"}
    if ctype == "ElasticSearch":    return {"host": d, "port": "9200"}
    if ctype == "Pinecone":         return {"apiKey": "dummy", "environment": "us-east-1"}
    if ctype == "RemoteMCP":        return {"url": "https://" + d}
    if ctype == "SharePointOnline": return {"tenantId": "dummy", "clientId": "dummy", "clientSecret": "x", "sharepointSite": "dummy"}
    return {}

# --------------------------------------------------------------------------- #
# Small utilities
# --------------------------------------------------------------------------- #
_print_lock = threading.Lock()
def log(msg):
    with _print_lock:
        print(msg, flush=True)

def scaled(n, scale): return max(0, int(round(n * scale)))

def zipf_distribution(total, n_buckets, cap=None):
    """Deterministic skewed allocation of `total` across `n_buckets` (Zipf-ish:
    a few heavy buckets, a long light tail, some zeros). Sums exactly to total."""
    if n_buckets <= 0 or total <= 0:
        return [0] * max(0, n_buckets)
    weights = [1.0 / (i + 1) for i in range(n_buckets)]
    s = sum(weights)
    raw = [total * w / s for w in weights]
    counts = [int(math.floor(x)) for x in raw]
    if cap is not None:
        counts = [min(c, cap) for c in counts]
    # distribute rounding remainder to the largest fractional parts
    deficit = total - sum(counts)
    order = sorted(range(n_buckets), key=lambda i: raw[i] - math.floor(raw[i]), reverse=True)
    idx = 0
    while deficit > 0 and idx < len(order) * 4:
        i = order[idx % len(order)]
        if cap is None or counts[i] < cap:
            counts[i] += 1; deficit -= 1
        idx += 1
    # interleave so heavy buckets aren't all at the front (deterministic shuffle)
    interleaved = [0] * n_buckets
    for k, i in enumerate(range(n_buckets)):
        interleaved[(k * 2654435761) % n_buckets] = counts[i]
    return interleaved

def run_pool(items, fn, workers, label):
    """Map fn over items with a thread pool; return (ok, fail) counts."""
    ok = fail = 0
    done = 0
    total = len(items)
    if total == 0:
        return 0, 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(fn, it): it for it in items}
        for fut in cf.as_completed(futs):
            done += 1
            try:
                if fut.result() is False: fail += 1
                else: ok += 1
            except Exception as e:
                fail += 1
                log(f"    ! {label}: {repr(e)[:140]}")
            if done % max(1, total // 20) == 0 or done == total:
                log(f"    {label}: {done}/{total} ({ok} ok, {fail} fail)")
    return ok, fail

# --------------------------------------------------------------------------- #
# The populator
# --------------------------------------------------------------------------- #
class Populator:
    def __init__(self, client, prefix, profile, scale, workers, dry_run):
        self.c = client
        self.p = prefix
        self.prof = profile
        self.scale = scale
        self.workers = workers
        self.dry = dry_run
        self._bad_interp = set()      # interpreters known-missing on this host
        self._fallback_interp = None  # first working python interpreter
        self.summary = {}

    # ---- helpers ---------------------------------------------------------- #
    def _has_prefix(self, name): return name and name.startswith(self.p)

    def _existing_connections(self):
        return {n for n, _ in self.c.list_connections().items() if self._has_prefix(n)}

    def _existing_code_envs(self):
        out = set()
        for e in self.c.list_code_envs():
            name = e.get("envName") or e.get("name")
            if self._has_prefix(name):
                out.add(name)
        return out

    def _existing_groups(self):
        return {g["name"] for g in self.c.list_groups() if self._has_prefix(g.get("name"))}

    def _existing_users(self):
        return {u["login"] for u in self.c.list_users() if self._has_prefix(u.get("login"))}

    def _existing_projects(self):
        return {k for k in self.c.list_project_keys() if self._has_prefix(k)}

    # ---- stages ----------------------------------------------------------- #
    def groups(self):
        want = [f"{self.p}group_{i:03d}" for i in range(scaled(self.prof["groups"], self.scale))]
        have = self._existing_groups()
        todo = [g for g in want if g not in have]
        log(f"[groups] target {len(want)}, existing {len(have & set(want))}, creating {len(todo)}")
        if self.dry or not todo:
            self.summary["groups"] = len(want); return
        def mk(name):
            self.c.create_group(name, description="dummy load-test group")
        run_pool(todo, mk, self.workers, "groups")
        self.summary["groups"] = len(self._existing_groups())

    def users(self):
        total = scaled(self.prof["users_total"], self.scale)
        enabled = scaled(self.prof["users_enabled"], self.scale)
        # build profile assignment list of length `enabled`
        profiles = []
        for prof, cnt in self.prof["user_profiles"].items():
            profiles += [prof] * scaled(cnt, self.scale)
        profiles = (profiles + ["FULL_DESIGNER"] * total)[:enabled]
        groups = sorted(self._existing_groups()) or None
        specs = []
        for i in range(total):
            login = f"{self.p}user_{i:04d}"
            is_enabled = i < enabled
            prof = profiles[i] if i < len(profiles) else "FULL_DESIGNER"
            specs.append((login, is_enabled, prof))
        have = self._existing_users()
        todo = [s for s in specs if s[0] not in have]
        log(f"[users] target {total} ({enabled} enabled), existing {len(have & {s[0] for s in specs})}, creating {len(todo)}")
        if self.dry or not todo:
            self.summary["users"] = total; return
        def mk(spec):
            login, is_enabled, prof = spec
            grp = None
            if groups:  # spread across groups deterministically
                grp = [groups[int(login[-2:], 36) % len(groups)]]
            n = login.split("_")[-1]
            self.c.create_user(login, "Dummy!Passw0rd-" + n,
                               display_name=f"Dummy User {n}", groups=grp,
                               profile=prof, email=f"dummy.user.{n}@example.invalid")
            if not is_enabled:
                try:
                    u = self.c.get_user(login); st = u.get_settings()
                    st.enabled = False; st.save()
                except Exception:
                    pass
        run_pool(todo, mk, self.workers, "users")
        self.summary["users"] = len(self._existing_users())

    def connections(self):
        # working Filesystem substrate for datasets/folders (kept separate + labelled)
        subs = [f"{self.p}fsdata_{i:02d}" for i in range(max(1, min(8, self.workers)))]
        # dummy typed connections matching the profile distribution
        typed = []
        for ctype, cnt in self.prof["connections_by_type"].items():
            for i in range(scaled(cnt, self.scale)):
                typed.append((f"{self.p}{ctype.lower()}_{i:02d}", ctype))
        want_names = set(subs) | {n for n, _ in typed}
        have = self._existing_connections()
        log(f"[connections] {len(subs)} working FS + {len(typed)} typed dummies; "
            f"existing {len(have & want_names)}, creating {len(want_names - have)}")
        if self.dry:
            self.summary["connections"] = len(want_names); self.fs_conns = subs; return
        def mk_fs(name):
            if name in have: return
            self.c.create_connection(name, "Filesystem", params={"root": "/tmp/" + name})
        def mk_typed(spec):
            name, ctype = spec
            if name in have: return
            for t in (ctype, CONN_TYPE_FALLBACK.get(ctype)):
                if not t: break
                try:
                    self.c.create_connection(name, t, params=conn_params(t)); return
                except Exception as e:
                    last = e
            log(f"    - skip {ctype} ({repr(last)[:60]})"); return False
        run_pool(subs, mk_fs, self.workers, "fs-conns")
        run_pool(typed, mk_typed, self.workers, "typed-conns")
        self.fs_conns = subs
        self.summary["connections"] = len(self._existing_connections())

    def projects(self):
        n = scaled(self.prof["projects"], self.scale)
        want = [f"{self.p}PROJ_{i:04d}" for i in range(n)]
        have = self._existing_projects()
        users = sorted(self._existing_users()) or ["admin"]
        todo = [k for k in want if k not in have]
        log(f"[projects] target {n}, existing {len(have & set(want))}, creating {len(todo)}")
        self.project_keys = want
        if self.dry or not todo:
            self.summary["projects"] = n; return
        def mk(pk):
            idx = int(pk.split("_")[-1])
            owner = users[idx % len(users)]
            self.c.create_project(pk, f"Dummy Project {pk.split('_')[-1]}",
                                  owner=owner, description="dummy load-test project")
        run_pool(todo, mk, self.workers, "projects")
        self.summary["projects"] = len(self._existing_projects())

    def _per_project(self, total_key, name_stem, make_one, cap=None, summary_key=None):
        """Shared engine for datasets / folders / saved-models: distribute a
        total across the dummy projects (skewed), create the per-project delta."""
        summary_key = summary_key or total_key
        total = scaled(self.prof[total_key], self.scale)
        keys = getattr(self, "project_keys", sorted(self._existing_projects()))
        if not self.dry:  # real run: only projects that actually exist
            existing = self._existing_projects()
            keys = [k for k in keys if k in existing]
        if not keys:
            log(f"[{name_stem}] no dummy projects — run projects stage first"); return
        alloc = zipf_distribution(total, len(keys), cap=cap)
        planned = sum(alloc)
        log(f"[{name_stem}] target {total} across {len(keys)} projects "
            f"(max/proj {max(alloc) if alloc else 0})")
        if self.dry:
            self.summary[summary_key] = planned; return
        counter = Counter()
        def work(pair):
            pk, want_n = pair
            if want_n <= 0: return
            proj = self.c.get_project(pk)
            existing = {getattr(o, "name", None) if hasattr(o, "name") else o.get("name")
                        for o in make_one.list(proj)}
            made = 0
            for j in range(want_n):
                nm = f"{self.p}{name_stem}_{j:04d}"
                if nm in existing: continue
                try:
                    make_one.create(proj, nm, j)
                    made += 1
                except Exception as e:
                    log(f"    ! {pk}/{nm}: {repr(e)[:90]}"); break
            counter[pk] = made
        run_pool(list(zip(keys, alloc)), work, self.workers, name_stem)
        self.summary[summary_key] = sum(counter.values())

    def datasets(self):
        fs = getattr(self, "fs_conns", None) or sorted(
            n for n in self._existing_connections() if "fsdata" in n)
        if not fs:
            log("[datasets] no working Filesystem connection — run connections stage first"); return
        class _DS:
            @staticmethod
            def list(proj): return proj.list_datasets()
            @staticmethod
            def create(proj, nm, j):
                conn = fs[j % len(fs)]
                proj.create_dataset(nm, "Filesystem",
                                    params={"connection": conn, "path": "/" + nm,
                                            "filesFilter": {"mode": "ALL"}},
                                    formatType="csv")
        self._per_project("datasets", "ds", _DS, cap=190)

    def folders(self):
        fs = getattr(self, "fs_conns", None) or sorted(
            n for n in self._existing_connections() if "fsdata" in n)
        if not fs:
            log("[folders] no working Filesystem connection — run connections stage first"); return
        class _MF:
            @staticmethod
            def list(proj): return proj.list_managed_folders()
            @staticmethod
            def create(proj, nm, j):
                proj.create_managed_folder(nm, connection_name=fs[j % len(fs)])
        self._per_project("managed_folders", "mf", _MF, cap=20)

    def saved_models(self):
        types = []
        for t, cnt in self.prof["saved_models_by_type"].items():
            types += [t] * scaled(cnt, self.scale)
        seq = {"i": 0}
        lock = threading.Lock()
        class _SM:
            @staticmethod
            def list(proj):
                return [{"name": m.get("name")} for m in proj.list_saved_models()]
            @staticmethod
            def create(proj, nm, j):
                with lock:
                    t = types[seq["i"] % len(types)] if types else "Unknown"; seq["i"] += 1
                pred = MODEL_TYPE_TO_PRED.get(t)
                proj.create_mlflow_pyfunc_model(nm, prediction_type=pred)
        # distribute total saved models across projects
        self.prof.setdefault("saved_models", sum(self.prof["saved_models_by_type"].values()))
        self._per_project("saved_models", "sm", _SM, cap=None)

    def code_envs(self):
        specs = []  # (name, lang, desired_interp)
        for ver, cnt in self.prof["code_envs_python_by_version"].items():
            interp = PY_VERSION_TO_INTERP.get(ver)
            for i in range(scaled(cnt, self.scale)):
                specs.append((f"{self.p}env_py{ver.replace('.', '')}_{i:03d}", "PYTHON", interp))
        for i in range(scaled(self.prof["code_envs_r"], self.scale)):
            specs.append((f"{self.p}env_r_{i:03d}", "R", None))
        have = self._existing_code_envs()
        todo = [s for s in specs if s[0] not in have]
        log(f"[code_envs] target {len(specs)}, existing {len(have & {s[0] for s in specs})}, "
            f"creating {len(todo)} (~{len(todo)*4//60 + 1} min of builds)")
        if self.dry or not todo:
            self.summary["code_envs"] = len(specs); return

        def resolve_fallback():
            if self._fallback_interp: return self._fallback_interp
            for cand in INTERP_FALLBACKS:
                if cand in self._bad_interp: continue
                self._fallback_interp = cand
                return cand
            return None

        def mk(spec):
            name, lang, interp = spec
            params = {"installCorePackages": False, "installJupyterSupport": False,
                      "desc": {"installCorePackages": False, "installJupyterSupport": False}}
            tried = []
            if lang == "PYTHON":
                order = [interp] if interp and interp not in self._bad_interp else []
                fb = resolve_fallback()
                if fb and fb not in order: order.append(fb)
                if not order: order = [None]
            else:
                order = [None]  # R: let DSS pick; skip whole lang on failure
            for it in order:
                p = dict(params)
                if it: p["pythonInterpreter"] = it
                try:
                    self.c.create_code_env(lang, name, "DESIGN_MANAGED", params=p, wait=True)
                    return
                except Exception as e:
                    tried.append(it)
                    if it and ("interpreter" in repr(e).lower() or "not available" in repr(e).lower()
                               or "no python" in repr(e).lower()):
                        self._bad_interp.add(it)
                    last = e
            log(f"    - code env {name} failed (tried {tried}): {repr(last)[:90]}")
            return False

        # Code env builds are serial-ish on the host; keep concurrency low.
        run_pool(todo, mk, min(self.workers, 4), "code_envs")
        self.summary["code_envs"] = len(self._existing_code_envs())

    # ---- teardown --------------------------------------------------------- #
    def purge(self, confirm=True):
        """Delete ONLY objects carrying the prefix (nothing else). Tallies and
        shows exactly what will go first, then — unless --yes/--dry-run — asks."""
        def del_env(name):
            for lang in ("PYTHON", "R"):
                try:
                    self.c._perform_empty("DELETE", f"/admin/code-envs/{lang}/{name}"); return
                except Exception:
                    continue
        # gather everything up front so the plan is auditable BEFORE any deletion.
        # (datasets/folders/saved models live inside the dummy projects and are
        # removed transitively when the project is deleted.)
        plan = [
            ("projects", sorted(self._existing_projects()),
             lambda k: self.c.get_project(k).delete(clear_managed_datasets=True), self.workers),
            ("code_envs", sorted(self._existing_code_envs()), del_env, min(self.workers, 4)),
            ("connections", sorted(self._existing_connections()),
             lambda n: self.c.get_connection(n).delete(), self.workers),
            ("users", sorted(self._existing_users()),
             lambda n: self.c.get_user(n).delete(), self.workers),
            ("groups", sorted(self._existing_groups()),
             lambda n: self.c.get_group(n).delete(), self.workers),
        ]
        total = sum(len(items) for _, items, _, _ in plan)
        log(f"[purge] objects carrying prefix '{self.p}' (and nothing else):")
        for label, items, _, _ in plan:
            sample = ", ".join(items[:4]) + (" …" if len(items) > 4 else "")
            log(f"  {label:12s}: {len(items):5d}   {sample}")
        log(f"  {'TOTAL':12s}: {total:5d}")
        if total == 0:
            log("[purge] nothing to delete."); return
        if self.dry:
            log("[purge] dry-run — nothing deleted."); return
        if confirm:
            try:
                ans = input(f"Delete these {total} objects prefixed '{self.p}'? [y/N] ").strip().lower()
            except EOFError:
                ans = ""
            if ans not in ("y", "yes"):
                log("[purge] aborted — nothing deleted."); return
        for label, items, fn, workers in plan:
            if items:
                run_pool(items, fn, workers, "del-" + label)
        log("[purge] done.")


STAGES = ["groups", "users", "connections", "projects",
          "datasets", "folders", "saved_models", "code_envs"]

def resolve_creds(args):
    url = args.url or os.environ.get("DSS_URL")
    key = args.api_key or os.environ.get("DSS_API_KEY")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if not url and os.path.exists(os.path.join(root, ".dss-url")):
        url = open(os.path.join(root, ".dss-url")).read().strip()
    if not key and os.path.exists(os.path.join(root, ".dss-api-key")):
        key = open(os.path.join(root, ".dss-api-key")).read().strip()
    if not url or not key:
        sys.exit("Need a DSS URL and API key (--url/--api-key, $DSS_URL/$DSS_API_KEY, or .dss-url/.dss-api-key).")
    return url, key

def main():
    ap = argparse.ArgumentParser(description="Populate a DSS instance with dummy objects for scan testing.")
    ap.add_argument("--url"); ap.add_argument("--api-key")
    ap.add_argument("--prefix", default="DUMMY_", help="name prefix marking every created object (default DUMMY_)")
    ap.add_argument("--scale", type=float, default=1.0, help="multiply all target counts (e.g. 0.1 for a small test)")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--profile", help="JSON file overriding the embedded tam-global profile")
    ap.add_argument("--only", help="comma list of stages to run: " + ",".join(STAGES))
    ap.add_argument("--skip", help="comma list of stages to skip")
    ap.add_argument("--purge", "--delete", dest="purge", action="store_true",
                    help="delete ONLY objects carrying the prefix (nothing else), then exit")
    ap.add_argument("--yes", "-y", action="store_true", help="skip the purge confirmation prompt")
    ap.add_argument("--dry-run", action="store_true", help="plan only; create/delete nothing (pair with --purge to preview a teardown)")
    ap.add_argument("--insecure", action="store_true", default=True, help="skip TLS verify (default on; self-signed)")
    args = ap.parse_args()

    if len(args.prefix) < 3:
        sys.exit("Refusing to run: --prefix must be >=3 chars so purge/idempotency can't match real objects.")

    url, key = resolve_creds(args)
    import urllib3; urllib3.disable_warnings()
    client = dataikuapi.DSSClient(url, key)
    if args.insecure:
        client._session.verify = False

    prof = dict(DEFAULT_PROFILE)
    if args.profile:
        import json
        prof.update(json.load(open(args.profile)))

    try:
        info = client.get_instance_info().raw
        node = info.get("nodeId", "?")
    except Exception:
        node = "?"
    log(f"# target: {url}  (node={node})")
    log(f"# prefix: {args.prefix!r}   scale: {args.scale}   dry-run: {args.dry_run}")
    log(f"# profile source: {prof.get('source')}")

    pop = Populator(client, args.prefix, prof, args.scale, args.workers, args.dry_run)

    if args.purge:
        pop.purge(confirm=not args.yes); return

    stages = STAGES
    if args.only:  stages = [s for s in STAGES if s in args.only.split(",")]
    if args.skip:  stages = [s for s in stages if s not in args.skip.split(",")]

    t0 = time.time()
    for stage in stages:
        getattr(pop, stage)()
    dt = time.time() - t0

    log("\n=== summary ===")
    for k in ["groups", "users", "connections", "projects", "datasets",
              "managed_folders", "saved_models", "code_envs"]:
        if k in pop.summary:
            log(f"  {k:16s}: {pop.summary[k]}")
    log(f"  {'NOT faked':16s}: plugins={prof['plugins']} code_studios={prof['code_studios']} "
        f"(need real content — see header)")
    log(f"  elapsed: {dt:.1f}s")

if __name__ == "__main__":
    main()
