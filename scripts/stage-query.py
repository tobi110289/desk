#!/usr/bin/env python3
"""stage-query.py [--staging] <Entity> [--id=… --tenant=… --format=json …]: one keyed read, for a desk host.

  stage-query.py <Entity> …             reads production
  stage-query.py --staging <Entity> …   reads staging

On the laptop a read of a stage goes through the Orbit checkout's own wrapper (`STAGE=<stage> pnpm -C <orbit>
dynamo:query`). That wrapper starts `sst shell`, which reads the deploy state of the stage. A desk host holds
read-only credentials that may read the tables and the logs and nothing else, so `sst shell` fails there.

This script runs the same key resolver of the Orbit checkout and gives it what `sst shell` would: the stage and the
table names (config.json stageResources), under the AWS profile named like the stage. The other arguments go to
the resolver unchanged. It runs the resolver and nothing else: a read.

config.json on the desk host:
  "stageQuery": "python3 <desk folder>/stage-query.py"     desk-execute.py puts this command into the validation prompt
  "stageResources": {"production": {"PrimaryTable": "<table name>", "CachingTable": "…", "TrackingTable": "…"},
                     "staging": {…}}
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402

RESOLVER = 'scripts/dynamo/resolveKey.ts'


def resource_env(resources, stage):
    """The variables the `sst` package reads a linked resource from, one per resource."""
    env = {'SST_RESOURCE_App': json.dumps({'name': 'orbit', 'stage': stage})}
    for name, table in resources.items():
        env[f'SST_RESOURCE_{name}'] = json.dumps({'name': table, 'type': 'sst.aws.Dynamo'})
    return env


def split_args(argv):
    """(stage, the resolver's arguments). --staging may stand anywhere; without it the read is of production."""
    rest = [arg for arg in argv if arg != '--staging']
    return ('staging' if len(rest) < len(argv) else 'production'), rest


def main():
    stage, args = split_args(sys.argv[1:])
    resources = (deskconfig.CFG.get('stageResources') or {}).get(stage) or {}
    if not resources.get('PrimaryTable'):
        sys.exit(f"stage-query: config key 'stageResources' names no PrimaryTable for {stage}; this machine reads a "
                 'stage through the Orbit wrapper (STAGE=<stage> pnpm -C <orbit> dynamo:query)')
    orbit = deskconfig.need(deskconfig.ORBIT_DIR)
    deskconfig.need(os.path.join(orbit, RESOLVER), 'key resolver of the Orbit checkout')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_ACCESS', 'AWS_SECRET', 'AWS_SESSION', 'SST_'))}
    env.update(resource_env(resources, stage))
    env['AWS_PROFILE'] = stage
    env.setdefault('AWS_REGION', 'eu-central-1')
    ran = subprocess.run(['pnpm', '-s', '-C', orbit, 'exec', 'tsx', RESOLVER, *args], env=env)
    sys.exit(ran.returncode)


if __name__ == '__main__':
    main()
