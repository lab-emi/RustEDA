#!/usr/bin/env python3
# SPDX-License-Identifier: Apache-2.0
# SPDX-FileCopyrightText: 2026 RustEDA contributors
"""Build the unified, static documentation site from the pinned module trees."""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote, unquote, urlsplit

import yaml

ROOT = Path(__file__).resolve().parents[1]
MODULES = ('RustSys', 'RustSTA', 'RustROAD', 'RustLane', 'RustEDA-Studio', 'RustEDA-Common')
MAX_FILES = 2500
MAX_BYTES = 256 * 1024 * 1024
SUFFIXES = {'.md', '.html', '.svg', '.gif', '.png', '.jpg', '.css', '.js', '.json', '.time', '.txt'}


def git_head(repo):
    return subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()


def build(stage, site):
    for path in (stage, site):
        if path.exists():
            shutil.rmtree(path)
        path.mkdir(parents=True)
    files = {}
    roots = [(ROOT, '')] + [(ROOT/'modules'/name, 'modules/'+name.lower()) for name in MODULES]
    total = 0
    for repo, prefix in roots:
        for src in sorted((repo/'docs').rglob('*')):
            if not src.is_file() or src.suffix not in SUFFIXES:
                continue
            rel = src.relative_to(repo/'docs')
            if any(part.startswith(('.', '_')) for part in rel.parts) or src.name in ('requirements.txt', 'requirements.in'):
                continue
            total += src.stat().st_size
            if len(files) >= MAX_FILES or total > MAX_BYTES:
                raise SystemExit('DOCS_BUDGET: documentation exceeds file or byte cap')
            files[src.resolve()] = stage/prefix/rel
    # The license remains available without exposing local coordination files.
    files[(ROOT/'LICENSE').resolve()] = stage/'license.txt'
    revisions = {repo: git_head(repo) for repo, _ in roots}

    def link(value, source, dest, html=False):
        url = urlsplit(value)
        if url.scheme or url.netloc or not url.path or url.path.startswith('/'):
            return value
        target = (source.parent/unquote(url.path)).resolve()
        suffix = ('?'+url.query if url.query else '') + ('#'+url.fragment if url.fragment else '')
        staged_target = (dest.parent/unquote(url.path)).resolve()
        if not html and staged_target in files.values():
            return value
        if target in files:
            import os
            output = files[target]
            if html and output.suffix == '.md':
                output = output.parent if output.name == 'index.md' else output.with_suffix('')
                return Path(os.path.relpath(output, dest.parent)).as_posix()+'/'+suffix
            return Path(os.path.relpath(output, dest.parent)).as_posix()+suffix
        # Code and benchmark archives stay in their owning repository at its pinned SHA.
        for repo, _ in reversed(roots):
            if target.is_relative_to(repo):
                relative = target.relative_to(repo)
                if not target.exists() and str(relative).startswith(('crates/core/', 'crates/formats/', 'crates/apps/rusteda-cli/')):
                    candidate = ROOT/'modules/RustEDA-Common'/relative
                    if candidate.exists():
                        repo = ROOT/'modules/RustEDA-Common'
                        target = candidate
                        relative = target.relative_to(repo)
                if not target.exists():
                    raise ValueError(f'Broken source link: {source.relative_to(ROOT)} -> {value}')
                name = 'RustEDA' if repo == ROOT else repo.name
                kind = 'tree' if target.is_dir() else 'blob'
                return f'https://github.com/lab-emi/{name}/{kind}/{revisions[repo]}/{quote(relative.as_posix())}'+suffix
        raise ValueError(f'Link escapes the source tree: {source} -> {value}')

    for src, dest in files.items():
        dest.parent.mkdir(parents=True, exist_ok=True)
        if src.suffix == '.html':
            content = re.sub(r'((?:href|src|srcset)=")([^"\s]+)(")', lambda m: m[1]+link(m[2], src, dest, html=True)+m[3], src.read_text())
            dest.write_text(content)
            continue
        if src.suffix != '.md':
            shutil.copyfile(src, dest)
            continue
        parts = re.split(r'(```.*?```|~~~.*?~~~)', src.read_text(), flags=re.S)
        for i in range(0, len(parts), 2):
            parts[i] = re.sub(r'\]\(([^\s)]+)\)', lambda m: ']('+link(m[1], src, dest)+')', parts[i])
            parts[i] = re.sub(r'((?:href|src|srcset)=")([^"\s]+)(")', lambda m: m[1]+link(m[2], src, dest)+m[3], parts[i])
        dest.write_text(''.join(parts))

    def prefix_nav(item, prefix):
        if isinstance(item, str):
            return item if '://' in item else prefix+'/'+item
        if isinstance(item, list):
            return [prefix_nav(x, prefix) for x in item]
        return {key: prefix_nav(value, prefix) for key, value in item.items()}

    def prune(item):
        if isinstance(item, str):
            return None if item.endswith('/roadmap.md') else item
        if isinstance(item, list):
            return [p for x in item if (p := prune(x)) is not None]
        result = {key: p for key, value in item.items() if (p := prune(value)) is not None}
        return result or None

    nav = [{'Overview': 'index.md'}, {'Get started': [{'Build': 'development.md'}, {'Run a flow': 'flows.md'}, {'Modules': 'modules.md'}]}]
    module_nav = []
    for name in MODULES:
        repo = ROOT/'modules'/name; prefix = 'modules/'+name.lower()
        source_config = repo/'docs/mkdocs.yml'
        if source_config.exists():
            entries = prune(prefix_nav(yaml.safe_load(source_config.read_text())['nav'], prefix))
        else:
            entries = [{'Overview': prefix+'/index.md'}]
            docs = sorted(p for p in (repo/'docs').rglob('*.md') if p.name != 'index.md')
            groups = {}
            for file in docs:
                rel = file.relative_to(repo/'docs').as_posix()
                title = next((line.lstrip('# ').strip() for line in file.read_text().splitlines() if line.startswith('# ')), rel)
                group = 'Reference' if rel.startswith('specs/') else 'Verification' if rel.startswith('verification/') else 'Guides'
                groups.setdefault(group, []).append({title: prefix+'/'+rel})
            entries.extend({group: pages} for group, pages in groups.items())
        module_nav.append({name.replace('-', ' '): entries})
    nav += [{'Modules': module_nav}, {'Evidence': [{'Benchmarks': 'benchmarks.md'}, {'Scope and qualification': 'verification.md'}]}, {'Project': [{'Architecture': 'shared-foundation.md'}, {'Visual identity': 'brand.md'}, {'Provenance': 'provenance.md'}]}]
    config = yaml.safe_load((ROOT/'docs/mkdocs.yml').read_text())
    config.update(docs_dir=str(stage), site_dir=str(site), nav=nav)
    generated = ROOT/'.work/mkdocs-unified.yml';generated.write_text(yaml.safe_dump(config, sort_keys=False))
    subprocess.run(['python3', '-m', 'mkdocs', 'build', '--strict', '-f', str(generated)], check=True, timeout=180)
    print(f'Built {len(files)} source files ({total/1024/1024:.1f} MiB) into {site}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'.work/docs-site')
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(ROOT/'.work'):
        parser.error('output must be inside the ignored .work directory')
    build(ROOT/'.work/docs-source', output)
