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


if __name__ == '__main__':
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith('test_'):
            try:
                fn(); print('ok  ', name)
            except AssertionError as e:
                failed += 1; print('FAIL', name, e)
    raise SystemExit(1 if failed else 0)
