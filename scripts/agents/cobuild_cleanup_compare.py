"""Host-macro-only cleanup fixtures; refuses any non-fixture delete candidate."""
import os
import pathlib
import secrets
import shutil


def fixtures(run):
    from atk_agent_common.policies import fs_paths, log_files
    root = pathlib.Path(os.environ['DIP_HOME'])
    c, tk = run.dss, run.tk
    suffix = secrets.token_hex(4)
    name = 'atk_ab_' + suffix
    key = 'ATKAB' + suffix.upper()
    project = None
    owned = []
    old_time = 315532800  # 1980; the 10,000-day filter excludes normal DSS artifacts.
    cases = {}
    try:
        project = c.create_project(key, 'ADTK cleanup comparison ' + suffix, 'admin')
        run.save({'capability': '_fixtures', 'status': 'created', 'project': key})
        for policy, action, group in [('tmp', 'tmp-cleanup', name), ('exports', 'exports-cleanup', name),
                                      ('joblogs', 'job-logs-cleanup', key)]:
            base = root / fs_paths.POLICY_ROOTS[policy][0] / group
            if base.exists():
                raise RuntimeError('Refusing pre-existing fixture directory')
            base.mkdir(parents=True)
            owned.append(base)
            candidate = base / 'comparison-artifact'

            def reset(policy=policy, candidate=candidate, group=group):
                candidate.mkdir(exist_ok=True)
                (candidate / 'marker.txt').write_text('ADTK comparison fixture\n')
                os.utime(candidate / 'marker.txt', (old_time, old_time))
                os.utime(candidate, (old_time, old_time))
                scan = fs_paths.scan_aged_entries(str(root), policy, group=group if policy == 'joblogs' else None,
                                                   min_age_days=10000, keep_last=0)
                assert scan['totalDirs'] == 1, 'Refusing a sweep containing other candidates'
                # The policy's one candidate must be our tiny marker directory.
                assert scan['totalBytes'] == len(b'ADTK comparison fixture\n')

            def verify(_, candidate=candidate):
                assert not candidate.exists()
                return {'owned_directory_deleted': True, 'other_candidates': 0}

            target = {'minAgeDays': 10000, 'keepLast': 0, 'maxDeleteGB': 1}
            if policy == 'joblogs':
                target['projectKey'] = key
            cases[action] = (target, reset, verify, 'One tiny owned artifact aged to 1980; pre-scan refuses extra candidates')

        logfile = root / 'run' / (name + '.log.1')
        if logfile.exists():
            raise RuntimeError('Refusing pre-existing log fixture')
        owned.append(logfile)

        def reset_log():
            logfile.write_text('ADTK comparison rotated log\n')
            os.utime(logfile, (old_time, old_time))
            scan = log_files.scan(str(root), roots=['run'], min_age_days=10000)
            assert scan['totalFiles'] == 1
            assert scan['roots']['run']['sample'] == [str(logfile)]

        def verify_log(_):
            assert not logfile.exists()
            return {'owned_rotated_log_deleted': True, 'other_candidates': 0}

        cases['log-cleanup'] = ({'roots': ['run'], 'minAgeDays': 10000, 'maxDeleteGB': 1},
                                reset_log, verify_log, 'One uniquely named 1980-dated rotated log; refuses extra candidates')

        base = root / 'webappruns' / key / 'comparison'
        if base.exists():
            raise RuntimeError('Refusing pre-existing webapp fixture')
        base.mkdir(parents=True)
        owned.append(root / 'webappruns' / key)
        old = base / 'run_1980-01-01-00-00-00-001'
        newest = base / 'run_2026-09-14-00-00-00-001'
        newest.mkdir()
        (newest / 'keep.txt').write_text('keep')

        def reset_runs():
            old.mkdir(exist_ok=True)
            (old / 'marker.txt').write_text('delete')
            os.utime(old / 'marker.txt', (old_time, old_time))
            os.utime(old, (old_time, old_time))
            scan = fs_paths.scan_webappruns(str(root), project_key=key, min_age_days=10000, keep_last_runs=1)
            assert scan['totalDirs'] == 1

        def verify_runs(_):
            assert not old.exists() and (newest / 'keep.txt').read_text() == 'keep'
            return {'old_run_removed': True, 'newest_run_preserved': True}

        cases['project-clear-webapp-runs'] = ({'projectKey': key, 'keepDays': 10000, 'keepLastRuns': 1},
                                             reset_runs, verify_runs, 'Owned old run removed; newest marker run must survive')
        with run.gates(list(cases)):
            for action, case in cases.items():
                run.action(action, *case)
    finally:
        errors = []
        for path in reversed(owned):
            try:
                if path.is_dir():
                    shutil.rmtree(path)
                elif path.exists():
                    path.unlink()
            except Exception as exc:
                errors.append('fixture path: ' + type(exc).__name__)
        if project:
            try:
                project.delete(clear_managed_datasets=True, clear_output_managed_folders=True)
            except Exception as exc:
                errors.append('project: ' + type(exc).__name__)
        run.save({'capability': '_fixtures', 'status': 'cleanup_failed' if errors else 'deleted',
                  'project': key, 'errors': errors})
