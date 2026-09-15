"""Two explicitly approved executions of print(6 * 7), with no execution retry."""
import time


def fixtures(run):
    from atk_agent_common import actuator, capability_routing as routing
    timings = {}
    verified = False
    target = {'code': 'print(6 * 7)', 'purpose': 'Compare the two user-approved Python executions; expected stdout is 42'}
    with run.gates(['python-run']):
        for provider in ('existing', 'cobuild'):
            started = time.monotonic()
            with routing.override(provider):
                plan = actuator.plan_admin_action(run.tk, action='python-run', target=target)
                assert plan.get('confirm_token'), 'Python plan refused'
                result = actuator.execute_admin_action(
                    run.tk, action='python-run', target=plan['canonicalTarget'],
                    confirm_flag=True, confirm_token=plan['confirm_token'],
                    agent_name='atk-cobuild-comparison')
            timings[provider] = round(time.monotonic() - started, 3)
            out = result.get('result', {})
            assert result.get('status') == 'ok'
            assert out.get('stdout') == '42\n' and out.get('stderr') == ''
            assert out.get('exitCode') == 0 and out.get('timedOut') is False
            if provider == 'cobuild':
                verified = (plan.get('executionRoute', {}).get('resultAcknowledged') is True
                            and result.get('executionRoute', {}).get('resultAcknowledged') is True)
            run.save({'capability': '_python_execution', 'status': 'verified',
                      'provider': provider, 'stdout': '42', 'exit_code': 0})
    run.save({'capability': 'python-run', 'status': 'same' if verified else 'needs_review',
              'scope': 'Exactly two approved executions of print(6 * 7), one per path; stdout, stderr, exit code and timeout checked',
              'attempt': 1, 'existing_seconds': timings['existing'], 'cobuild_seconds': timings['cobuild'],
              'request_and_result_verified': verified, 'transport': 'deployed HTTP bridge'})
