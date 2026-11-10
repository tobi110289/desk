#!/usr/bin/env python3
"""The AWS SSO session state, for the status line and the desk pane (2026-11-10, Tobias: "add a prominent sso expired
both down there and in the desk pane, so I see it more easily"; the unattended validation had failed on an expired login).

Reads the SSO token cache (~/.aws/sso/cache/*.json, the entries with a startUrl and an expiresAt), takes the latest
expiry, writes cache/sso-state.json in the desk folder and prints one line: `expired`, `expiring 12m`, `ok 7h` or
`none` (no token cached). When the token is expired, the role credentials in ~/.aws/cli/cache decide: while they
are valid, AWS calls work and the state stays `ok`. No network call.
"""
import datetime
import glob
import json
import os
import sys

DESK = os.environ.get('DESK_DIR') or os.path.expanduser('~/orbit-scratch/desk')
EXPIRING_MIN = 30


def state():
    latest = None
    for f in glob.glob(os.path.expanduser('~/.aws/sso/cache/*.json')):
        try:
            d = json.load(open(f))
        except Exception:
            continue
        if not d.get('startUrl') or not d.get('expiresAt'):
            continue
        try:
            exp = datetime.datetime.strptime(d['expiresAt'][:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
        except Exception:
            continue
        if latest is None or exp > latest:
            latest = exp
    now = datetime.datetime.now(datetime.timezone.utc)
    if latest is None or latest <= now:
        # The SSO token lives about 1 h, the role credentials it minted (~/.aws/cli/cache) up to 12 h. AWS calls
        # work as long as those hold, so the login counts as expired only when they are gone too. The earliest
        # expiry of the valid ones decides.
        role = None
        for f in glob.glob(os.path.expanduser('~/.aws/cli/cache/*.json')):
            try:
                raw = json.load(open(f))['Credentials']['Expiration']
                exp = datetime.datetime.strptime(raw[:19], '%Y-%m-%dT%H:%M:%S').replace(tzinfo=datetime.timezone.utc)
            except Exception:
                continue
            if exp > now and (role is None or exp < role):
                role = exp
        if role is not None:
            latest = role
    if latest is None:
        return {'state': 'none', 'minutes': 0, 'expiresAt': None}
    minutes = int((latest - now).total_seconds() // 60)
    s = 'expired' if minutes <= 0 else 'expiring' if minutes <= EXPIRING_MIN else 'ok'
    return {'state': s, 'minutes': max(0, minutes), 'expiresAt': latest.strftime('%Y-%m-%dT%H:%M:%SZ')}


def main():
    st = state()
    st['checkedAt'] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    if '--no-write' not in sys.argv:
        try:
            os.makedirs(f'{DESK}/cache', exist_ok=True)
            tmp = f'{DESK}/cache/sso-state.json.tmp'
            json.dump(st, open(tmp, 'w')); os.replace(tmp, f'{DESK}/cache/sso-state.json')
        except Exception:
            pass
    m = st['minutes']
    print(st['state'] if st['state'] in ('expired', 'none') else f"{st['state']} {m // 60}h{m % 60:02d}m" if m >= 60 else f"{st['state']} {m}m")
    return 0


if __name__ == '__main__':
    sys.exit(main())
