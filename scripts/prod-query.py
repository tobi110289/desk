#!/usr/bin/env python3
"""prod-query.py <Entity> [--id=… --tenant=… --format=json …]: one keyed production read, for a desk host.

On the laptop a production read goes through the Orbit checkout's own wrapper (`STAGE=production pnpm -C <orbit>
dynamo:query`). That wrapper starts `sst shell`, which reads the deploy state of the stage. A desk host holds a
read-only role that may read the tables and the logs and nothing else, so `sst shell` fails there.

This script runs the same key resolver of the Orbit checkout and gives it what `sst shell` would: the stage and the
table names (config.json prodResources), under the AWS profile config.json prodProfile names (default
"production"). The arguments go to the resolver unchanged. It runs the resolver and nothing else: a read.

config.json on the desk host:
  "prodQuery": "python3 <desk folder>/prod-query.py"      desk-execute.py puts this command into the validation prompt
  "prodResources": {"PrimaryTable": "<table name>", "CachingTable": "…", "TrackingTable": "…"}
"""
import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.realpath(__file__)))
import deskconfig  # noqa: E402

RESOLVER = 'scripts/dynamo/resolveKey.ts'


def resource_env(resources, stage='production'):
    """The variables the `sst` package reads a linked resource from, one per resource."""
    env = {'SST_RESOURCE_App': json.dumps({'name': 'orbit', 'stage': stage})}
    for name, table in resources.items():
        env[f'SST_RESOURCE_{name}'] = json.dumps({'name': table, 'type': 'sst.aws.Dynamo'})
    return env


def main():
    resources = deskconfig.CFG.get('prodResources') or {}
    if not resources.get('PrimaryTable'):
        sys.exit("prod-query: config key 'prodResources' names no PrimaryTable; this machine reads production through "
                 'the Orbit wrapper (STAGE=production pnpm -C <orbit> dynamo:query)')
    orbit = deskconfig.need(deskconfig.ORBIT_DIR)
    deskconfig.need(os.path.join(orbit, RESOLVER), 'key resolver of the Orbit checkout')
    env = {k: v for k, v in os.environ.items() if not k.startswith(('AWS_ACCESS', 'AWS_SECRET', 'AWS_SESSION', 'SST_'))}
    env.update(resource_env(resources))
    env['AWS_PROFILE'] = deskconfig.CFG.get('prodProfile') or 'production'
    env.setdefault('AWS_REGION', 'eu-central-1')
    ran = subprocess.run(['pnpm', '-s', '-C', orbit, 'exec', 'tsx', RESOLVER, *sys.argv[1:]], env=env)
    sys.exit(ran.returncode)


if __name__ == '__main__':
    main()
