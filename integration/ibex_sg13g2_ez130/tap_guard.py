#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
"""Unchanged serial T1 4GiB per-process AS guard, inside product ownership.

No new cgroup/session/daemon. AS is inherited per process, not an aggregate
descendant bound; the original 8GiB product cgroup and timeout own cleanup.
"""
from pathlib import Path
import os
import resource
import sys

if len(sys.argv) < 2:
    raise ValueError('TAP_GUARD_RUNNER_REQUIRED')
runner = Path(sys.argv[1]).resolve(strict=True)
if not runner.is_file() or runner.stat().st_size > 1 << 20:
    raise ValueError('TAP_GUARD_RUNNER_BUDGET')
limit = 4096 << 20
resource.setrlimit(resource.RLIMIT_AS, (limit, limit))
os.execv(sys.executable, [sys.executable, str(runner), *sys.argv[2:]])
