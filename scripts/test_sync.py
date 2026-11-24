#!/usr/bin/env python3
"""test_sync.py: the three-way merge of the loop state (desk-sync.py). No network, no files.

  python3 scripts/test_sync.py
"""
import importlib.util
import os

HERE = os.path.dirname(os.path.realpath(__file__))
spec = importlib.util.spec_from_file_location('desk_sync', f'{HERE}/desk-sync.py')
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)

ENTRY = {'phase': 'pr-open', 'status': 'PR open, review runs', 'note': 'n1', 'workspace': 'w1'}


def merged(mine, theirs, base=None):
    base = {'T-1': dict(ENTRY)} if base is None else base
    return sync.merge_threads(base, mine, theirs)


def test_a_close_on_the_desk_host_and_a_phase_change_here_both_survive():
    here = {'T-1': {**ENTRY, 'phase': 'done', 'workspace': 'w2'}}
    there = {'T-1': {**ENTRY, 'status': 'done; customer told', 'note': 'n1 | desk: sent'}}
    assert merged(here, there) == {'T-1': {'phase': 'done', 'status': 'done; customer told', 'note': 'n1 | desk: sent', 'workspace': 'w2'}}


def test_both_sides_wrote_the_caption_and_the_desk_host_wins():
    here = {'T-1': {**ENTRY, 'status': 'worker runs'}}
    there = {'T-1': {**ENTRY, 'status': 'waiting for the customer'}}
    assert merged(here, there)['T-1']['status'] == 'waiting for the customer'


def test_both_sides_wrote_the_phase_and_this_machine_wins():
    assert merged({'T-1': {**ENTRY, 'phase': 'done'}}, {'T-1': {**ENTRY, 'phase': 'blocked'}})['T-1']['phase'] == 'done'


def test_a_new_entry_on_either_side_arrives_on_both():
    out = merged({'T-1': dict(ENTRY), 'T-2': {'phase': 'new'}}, {'T-1': dict(ENTRY), 'T-3': {'phase': 'new'}})
    assert sorted(out) == ['T-1', 'T-2', 'T-3']


def test_an_entry_archived_here_leaves_the_state_on_both_sides():
    assert merged({}, {'T-1': dict(ENTRY)}) == {}


def test_an_entry_archived_here_but_captioned_on_the_desk_host_stays():
    there = {'T-1': {**ENTRY, 'status': 'reopened by the customer'}}
    assert merged({}, there) == there


def test_a_field_removed_on_one_side_is_removed():
    here = {'T-1': {k: v for k, v in ENTRY.items() if k != 'workspace'}}
    assert 'workspace' not in merged(here, {'T-1': dict(ENTRY)})['T-1']


def test_the_first_sync_takes_this_machine_as_the_start():
    doc = {'threads': {'T-1': dict(ENTRY)}}
    assert sync.merge_doc({'threads': {}}, doc, doc) == doc


def _run_forwarding_script(args, extra_env=None):
    """A script that calls deskconfig.forward(), started with a config that names a desk host and a stand-in `ssh` on
    PATH that records its arguments and exits 7. Returns (exit code, what ssh got, the script's own output)."""
    import json, subprocess, sys, tempfile
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(f'{tmp}/bin'); os.makedirs(f'{tmp}/desk')
        open(f'{tmp}/bin/ssh', 'w').write('#!/bin/sh\nprintf "%s\\n" "$@" >> "$SSH_LOG"\nexit 7\n'); os.chmod(f'{tmp}/bin/ssh', 0o755)
        json.dump({'deskHost': 'the-desk-host'}, open(f'{tmp}/config.json', 'w'))
        script = f'{tmp}/desk/change.py'
        open(script, 'w').write(f'import sys\nsys.path.insert(0, {HERE!r})\nimport deskconfig\ndeskconfig.forward(__file__)\nprint("ran here")\n')
        env = {**os.environ, 'PATH': f'{tmp}/bin:' + os.environ['PATH'], 'SSH_LOG': f'{tmp}/ssh.log', 'DESK_DIR': f'{tmp}/desk',
               'DESK_SCRATCH': tmp, 'DESK_CONFIG': f'{tmp}/config.json', **(extra_env or {})}
        env.pop('DESK_FORWARDED', None) if not extra_env else None
        ran = subprocess.run([sys.executable, script, *args], env=env, capture_output=True, text=True)
        log = open(f'{tmp}/ssh.log').read() if os.path.exists(f'{tmp}/ssh.log') else ''
        return ran.returncode, log.replace(tmp, '<tmp>'), ran.stdout


def test_a_changing_script_runs_on_the_desk_host_with_its_arguments_and_its_exit_code():
    code, ssh, out = _run_forwarding_script(['loop:T-1', 'Send it now'])
    assert code == 7 and 'ran here' not in out, (code, out)
    assert 'the-desk-host' in ssh.splitlines()
    assert "python3 <tmp>/desk/change.py loop:T-1 'Send it now'" in ssh, ssh
    assert 'DESK_SCRATCH=<tmp>' in ssh


def test_on_the_desk_host_the_script_runs_in_place():
    code, ssh, out = _run_forwarding_script(['x'], {'DESK_FORWARDED': '1'})
    assert code == 0 and 'ran here' in out and ssh == '', (code, out, ssh)


def test_the_pull_never_touches_what_belongs_to_this_machine():
    argv = sync.pull_argv('/desk', 'h')
    kept = [argv[i + 1] for i, a in enumerate(argv) if a == '--exclude']
    for name in ('*.py', 'config.json', '.env', '.auto', 'notion-tickets.json', 'cache/sso-state.json'):
        assert name in kept, name
    assert argv[-2].endswith('/desk/') and argv[-1] == '/desk/'


if __name__ == '__main__':
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            try:
                fn(); print('ok  ', name)
            except AssertionError as e:
                failed += 1; print('FAIL', name, e)
    raise SystemExit(1 if failed else 0)
