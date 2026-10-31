# SPDX-License-Identifier: Apache-2.0
"""Bounded byte identities for the explicit foundry-first protocol.

No EDA semantics are inferred here. One64KiB hash buffer, <=64 revision files,
512MiB total revision bytes, <=2MiB JSON metadata and a64KiB native header.
Only the qualified format6/seven-file snapshot is admitted for reuse. All
unknown schemas/inventories are explicit cache misses, never normalization.
"""
from contextlib import contextmanager
import codecs
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import stat

EXTRACT_OPTIONS = frozenset({'--threads', '--max-expansions', '--rc-profile', '--corner',
    '--max-spef-mib', '--extract-memory-mib', '--max-extract-shapes', '--max-rc-nodes',
    '--max-rc-elements', '--max-coupling-caps', '--max-extract-work', '--max-extract-candidates',
    '--min-coupling-ff', '--max-segment-um', '--extract-query-workers', '--extract-query-batch-runs',
    '--extract-query-scratch-mib'})

JSON_CAP = 2 << 20
SNAPSHOT_CAP = 512 << 20
REV_FILES = frozenset({'design.json', 'instances.rbin', 'pins.rbin',
                       'net_pins.rbin', 'net_offsets.rbin', 'physical.rbin', 'routes.rbin'})
# These exact qualified engines were independently audited for the complete
# format6+RC-profile input closure. A future ELF does not inherit that audit.
KNOWN_ENGINES = {
    'c2ae7177ad0a55ab4527bbe63b5c7f38fca91a31c50367cb2a2887bc0ce895cb':
        dict(bytes=58259096, queries='qualified_serial_defaults'),
    'f47c5a7526855a4a5a106dd2ed42c03c74a2ace0ed48457b6942be1ec2d0110c':
        dict(bytes=58686880, queries='explicit_queries_required'),
}
BOUND_DEFAULTS = dict(max_cells=250000, max_input_mib=16, max_rows=100000,
    max_nets=250000, max_pins=1000000, max_aliases=500000,
    max_extract_bin_entries=8000000, max_virtual_shapes=256000000,
    max_instance_bin_entries=64000000, max_segment_um=25.0)
POLICY = dict(schema='rusteda.foundry-first-policy.v1',
              repair='foundry_clean_keep_revision', rc='identical_whole_input',
              native_analysis='unchecked_unless_executed', max_snapshot_bytes=SNAPSHOT_CAP,
              max_snapshot_files=64, max_gds_bytes=64 << 20, max_spef_bytes=256 << 20)


class Miss(ValueError):
    """Unsupported but bounded input: execute the unchanged legacy path."""


def need(ok, message):
    if not ok:
        raise ValueError(message)


def read(path, cap=JSON_CAP):
    path = real_path(path)
    need(path.is_file() and path.stat().st_size <= cap, 'JSON_BYTE_BUDGET')
    def unique(rows):
        obj = {}
        for key, value in rows:
            need(key not in obj, 'JSON_DUPLICATE_KEY')
            obj[key] = value
        return obj
    def nonfinite(value):
        raise ValueError('JSON_NONFINITE')
    with path.open('rb') as stream:
        raw = stream.read(cap + 1)
    need(len(raw) <= cap, 'JSON_BYTE_BUDGET')
    return json.loads(raw, object_pairs_hook=unique, parse_constant=nonfinite)


def write_new(path, value):
    raw = (json.dumps(value, indent=2, allow_nan=False) + '\n').encode()
    need(len(raw) <= JSON_CAP, 'JSON_OUTPUT_BUDGET')
    with Path(path).open('xb') as stream:
        stream.write(raw)


def real_path(path):
    """Check every actual component before resolve can erase symlink evidence."""
    path = Path(path).absolute()
    need('..' not in path.parts, 'FILE_PARENT_TRAVERSAL')
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        need(not stat.S_ISLNK(current.lstat().st_mode), 'FILE_SYMLINK')
    return path.resolve(strict=True)


def identity(path, cap=4 << 30):
    path = real_path(path)
    before = path.stat()
    need(stat.S_ISREG(before.st_mode) and 0 <= before.st_size <= cap, 'FILE_BYTE_BUDGET')
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        need(os.fstat(stream.fileno()).st_ino == before.st_ino, 'FILE_OPEN_RACE')
        remaining = before.st_size
        while remaining:
            chunk = stream.read(min(65536, remaining))
            need(chunk, 'FILE_CHANGED')
            digest.update(chunk)
            remaining -= len(chunk)
        need(stream.read(1) == b'', 'FILE_CHANGED')
    after = path.stat()
    need((before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) ==
         (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns), 'FILE_CHANGED')
    return dict(path=str(path), sha256=digest.hexdigest(), bytes=after.st_size)


def record(value, cap=4 << 30):
    file_id_shape(value, cap)
    need(identity(value['path'], cap) == value, 'FILE_ID_CHANGED')
    return Path(value['path'])


def file_id_shape(value, cap=4 << 30):
    need(isinstance(value, dict) and set(value) == {'path', 'sha256', 'bytes'}
         and isinstance(value['path'], str) and Path(value['path']).is_absolute()
         and isinstance(value['sha256'], str) and re.fullmatch('[0-9a-f]{64}', value['sha256'])
         and type(value['bytes']) is int and 0 <= value['bytes'] <= cap, 'FILE_ID_SCHEMA')


def private_path(path, owner, create_parents=False):
    """Admit every existing ancestor before the first mkdir/open/write."""
    owner = real_path(owner)
    path = Path(path).absolute()
    need('..' not in path.parts and path.is_relative_to(owner), 'WRITER_OUTSIDE_OWNER')
    current = owner
    parts = path.relative_to(owner).parts
    for index, component in enumerate(parts):
        current /= component
        try:
            mode = current.lstat().st_mode
        except FileNotFoundError:
            if index < len(parts) - 1:
                need(create_parents, 'WRITER_PARENT_MISSING')
                current.mkdir()
            continue
        need(not stat.S_ISLNK(mode), 'WRITER_SYMLINK')
        if index < len(parts) - 1:
            need(stat.S_ISDIR(mode), 'WRITER_PARENT_NOT_DIRECTORY')
    return path


def seal_owned(path, owner, cap):
    """Admit an owned unique inode before chmod; never alter an external alias."""
    path = private_path(path, owner)
    value = identity(path, cap)
    need(path.stat().st_nlink == 1, 'ARTIFACT_SHARED_INODE')
    path.chmod(0o444)
    immutable(value, owner, cap)
    return value


def revision_files(folder):
    result = []
    for item in folder.iterdir():
        if len(result) >= 64:
            raise Miss('SNAPSHOT_FILE_COUNT')
        result.append(item)
    return result


def same_bytes(a, b):
    return (a['sha256'], a['bytes']) == (b['sha256'], b['bytes'])


def validate_policy(value):
    need(value == POLICY and set(value) == set(POLICY), 'FOUNDRY_FIRST_POLICY')
    need(all(type(value[k]) is int for k in
             ('max_snapshot_bytes', 'max_snapshot_files', 'max_gds_bytes', 'max_spef_bytes')),
         'FOUNDRY_FIRST_POLICY_INTEGER')


@contextmanager
def shared(workspace):
    workspace = real_path(workspace)
    need(not (workspace / 'workspace.lock').is_symlink(), 'WORKSPACE_LOCK_SYMLINK')
    # Never create a missing input workspace or silently adopt an absent lock.
    with (workspace / 'workspace.lock').open('r+b') as lock:
        fcntl.flock(lock, fcntl.LOCK_SH)
        try:
            yield workspace
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def prefix(path):
    """Only the four leading scalar fields of canonical native metadata."""
    with Path(path).open('rb') as stream:
        raw = stream.read(65536)
    text = codecs.getincrementaldecoder('utf-8')().decode(raw, final=False)
    pattern = r'\A\{"format_version":(\d+),"revision":"([A-Za-z0-9_-]+)","parent_revision":(null|"[A-Za-z0-9_-]+"),"stage":"([A-Za-z0-9_-]+)"[,:]'
    match = re.match(pattern, text)
    if match is None:
        raise Miss('SNAPSHOT_HEADER_UNSUPPORTED')
    return dict(format_version=int(match[1]), revision=match[2],
                parent_revision=json.loads(match[3]), stage=match[4])


def snapshot_locked(workspace):
    """Caller holds shared(workspace); paths are excluded from content equality."""
    workspace = real_path(workspace)
    head = identity(workspace / 'HEAD', 128)
    rev = Path(head['path']).read_text()
    need(re.fullmatch(r'[A-Za-z0-9_-]+\n', rev) is not None, 'HEAD_HANDLE')
    rev = rev[:-1]
    folder = workspace / 'revisions' / rev
    need(not folder.is_symlink() and folder.is_dir(), 'REVISION_DIRECTORY')
    folder = real_path(folder)
    children = revision_files(folder)
    if {p.name for p in children} != REV_FILES:
        raise Miss('SNAPSHOT_INVENTORY_UNSUPPORTED')
    for path in children:
        value = path.lstat()
        need(stat.S_ISREG(value.st_mode), 'SNAPSHOT_REGULAR_FILES_REQUIRED')
        if value.st_nlink != 1:
            raise Miss('SNAPSHOT_SHARED_INODE')
    header = prefix(folder / 'design.json')
    if header['format_version'] != 6 or header['stage'] != 'placed':
        raise Miss('SNAPSHOT_SCHEMA_UNSUPPORTED')
    need(header['revision'] == rev, 'SNAPSHOT_REVISION_CONFLICT')
    sizes = [p.lstat().st_size for p in children]
    if sum(sizes) > SNAPSHOT_CAP:
        raise Miss('SNAPSHOT_BYTE_BUDGET')
    rows = []
    for path in sorted(children):
        item = identity(path, SNAPSHOT_CAP)
        rows.append(dict(name=path.name, sha256=item['sha256'], bytes=item['bytes']))
    need(identity(workspace / 'HEAD', 128) == head, 'HEAD_CHANGED')
    need({p.name for p in revision_files(folder)} == REV_FILES, 'SNAPSHOT_FILES_CHANGED')
    value = dict(schema='rusteda.full-revision-input.v1', revision=rev,
                 metadata=header, head_sha256=head['sha256'], files=rows)
    validate_snapshot(value)
    return value


def validate_snapshot(value):
    need(isinstance(value, dict) and set(value) == {'schema', 'revision', 'metadata', 'head_sha256', 'files'}
         and value['schema'] == 'rusteda.full-revision-input.v1'
         and isinstance(value['revision'], str) and re.fullmatch('[A-Za-z0-9_-]+', value['revision']),
         'SNAPSHOT_PROOF_SCHEMA')
    metadata = value['metadata']
    need(set(metadata) == {'format_version', 'revision', 'parent_revision', 'stage'}
         and type(metadata['format_version']) is int and metadata['format_version'] == 6
         and metadata['revision'] == value['revision'] and metadata['stage'] == 'placed'
         and (metadata['parent_revision'] is None or isinstance(metadata['parent_revision'], str)
              and re.fullmatch('[A-Za-z0-9_-]+', metadata['parent_revision'])), 'SNAPSHOT_PROOF_METADATA')
    need(value['head_sha256'] == hashlib.sha256((value['revision'] + '\n').encode()).hexdigest(),
         'SNAPSHOT_PROOF_HEAD')
    files = value['files']
    need(isinstance(files, list) and len(files) == len(REV_FILES)
         and [r['name'] for r in files] == sorted(REV_FILES), 'SNAPSHOT_PROOF_INVENTORY')
    total = 0
    for row in files:
        need(set(row) == {'name', 'sha256', 'bytes'} and type(row['bytes']) is int and row['bytes'] >= 0
             and isinstance(row['sha256'], str) and re.fullmatch('[0-9a-f]{64}', row['sha256']),
             'SNAPSHOT_PROOF_COUNTER')
        total += row['bytes']
        need(total <= SNAPSHOT_CAP, 'SNAPSHOT_PROOF_BYTE_BUDGET')


def snapshot(workspace):
    with shared(workspace) as locked:
        return snapshot_locked(locked)


def freeze_file(source, destination, owner, cap):
    """Owned immutable effective input, never a mutable hard link or symlink."""
    owner = real_path(owner)
    destination = private_path(destination, owner)
    need(not destination.exists() and not destination.is_symlink(), 'FREEZE_DESTINATION_EXISTS')
    before = identity(source, cap)
    with Path(before['path']).open('rb') as src, destination.open('xb') as dst:
        remaining = before['bytes']
        while remaining:
            chunk = src.read(min(65536, remaining))
            need(chunk, 'FREEZE_SOURCE_CHANGED')
            dst.write(chunk)
            remaining -= len(chunk)
        need(src.read(1) == b'', 'FREEZE_SOURCE_CHANGED')
    need(identity(source, cap) == before, 'FREEZE_SOURCE_CHANGED')
    actual = identity(destination, cap)
    need(same_bytes(before, actual), 'FREEZE_COPY_CHANGED')
    need(destination.stat().st_nlink == 1, 'FREEZE_HARD_LINK')
    destination.chmod(0o444)
    return dict(source=before, effective=actual, ownership='private_immutable_full_interval')


def immutable(record_value, owner, cap):
    path = record(record_value, cap)
    need(path.is_relative_to(real_path(owner)) and path.stat().st_nlink == 1
         and path.stat().st_mode & 0o222 == 0, 'EFFECTIVE_INPUT_NOT_FROZEN')
    return path


def freeze_profile(source, destination, owner):
    """Opaque prepared-deck copy; only inventory metadata/hashes are read.

    No deck code is interpreted. The existing original DRC wrapper validates
    this exact copied inventory before and after black-box execution.
    """
    source = record(source, JSON_CAP)
    data = read(source)
    need(data['original_views_modified'] is False and isinstance(data['files'], dict)
         and 1 <= len(data['files']) <= 4096, 'FOUNDRY_PROFILE_INVENTORY')
    destination = private_path(destination, owner, create_parents=True)
    need(not destination.exists(), 'FOUNDRY_PROFILE_DESTINATION_EXISTS')
    destination.mkdir()
    rows, total = [], 0
    for name, spec in sorted(data['files'].items()):
        relative = Path(name)
        need(relative.parts and not relative.is_absolute() and '..' not in relative.parts
             and len(relative.parts) <= 32, 'FOUNDRY_PROFILE_PATH')
        original = real_path(source.parent / 'klayout' / relative)
        target = private_path(destination / 'klayout' / relative, owner, create_parents=True)
        # The remaining aggregate allowance is checked by freeze_file before
        # opening the destination, even if the source grew since enumeration.
        row = freeze_file(original, target, owner, (256 << 20) - total)
        total += row['effective']['bytes']
        need(total <= 256 << 20, 'FOUNDRY_PROFILE_BYTE_BUDGET')
        need(row['effective']['sha256'] == spec['prepared_sha256'], 'FOUNDRY_PROFILE_SOURCE_CHANGED')
        rows.append(row)
    profile = freeze_file(source, destination / 'provenance.json', owner, JSON_CAP)
    return dict(schema='rusteda.immutable-foundry-profile.v1', source=profile['source'],
                effective=profile['effective'], files=rows, bytes=total)


def argument_profile(argv, tool):
    """Bind admitted omissions to an exact audited ELF; never add argv tokens."""
    file_id_shape(tool)
    known = KNOWN_ENGINES.get(tool['sha256'])
    if known is None or tool['bytes'] != known['bytes']:
        raise Miss('EXTRACT_ENGINE_INPUT_CLOSURE_UNREVIEWED')
    queries = ('--extract-query-workers', '--extract-query-batch-runs', '--extract-query-scratch-mib')
    explicit_queries = all(option in argv for option in queries)
    if any(option in argv for option in queries) and not explicit_queries:
        raise Miss('EXTRACT_DEFAULTS_UNQUALIFIED')
    if not explicit_queries and known['queries'] != 'qualified_serial_defaults':
        raise Miss('EXTRACT_DEFAULTS_UNQUALIFIED')
    explicit_threads = '--threads' in argv
    if not explicit_threads and known['queries'] != 'qualified_serial_defaults':
        raise Miss('EXTRACT_DEFAULTS_UNQUALIFIED')
    threads = int(argv[argv.index('--threads') + 1]) if explicit_threads else 2
    need(1 <= threads <= 64, 'EXTRACT_POOL_BUDGET')
    return dict(schema='rusteda.qualified-extract-argument-profile.v1',
        engine=dict(sha256=tool['sha256'], bytes=tool['bytes']),
        input_closure='qualified_format6_seven_files_plus_rc_profile',
        cli_threads=threads,
        cli_threads_origin='explicit' if explicit_threads else 'qualified_elf_default',
        queries='explicit' if explicit_queries else 'qualified_serial_no_query_flags',
        other_omitted_defaults={key: value for key, value in BOUND_DEFAULTS.items()
                               if '--' + key.replace('_', '-') not in argv})


def extraction_request(argv, tool=None):
    """Normalize exactly workspace/out; all other ordered bytes must match."""
    need(isinstance(argv, list) and 1 <= len(argv) <= 128
         and all(isinstance(v, str) and len(v.encode()) <= 65536 for v in argv), 'EXTRACT_ARGV_BUDGET')
    need(argv.count('--workspace') == argv.count('--out') == argv.count('extract') == 1,
         'EXTRACT_ARGV_SHAPE')
    need(argv[1] == '--workspace', 'EXTRACT_WORKSPACE_POSITION')
    # A future option cannot be silently accepted by this protocol version.
    index = 3
    seen = set()
    operation = False
    while index < len(argv):
        token = argv[index]
        if token == 'extract' and not operation:
            operation = True
            index += 1
            continue
        if token not in EXTRACT_OPTIONS | {'--out'} or token in seen or index + 1 >= len(argv):
            raise Miss('EXTRACT_OPTION_UNSUPPORTED')
        seen.add(token)
        index += 2
    if not operation:
        raise Miss('EXTRACT_OPERATION_REQUIRED')
    if '--threads' in seen and argv.index('--threads') > argv.index('extract'):
        raise Miss('EXTRACT_GLOBAL_THREADS_REQUIRED')
    if not {'--max-expansions', '--rc-profile', '--corner', '--out', '--max-spef-mib',
            '--extract-memory-mib', '--max-extract-shapes', '--max-rc-nodes', '--max-rc-elements',
            '--max-coupling-caps', '--max-extract-work', '--max-extract-candidates',
            '--min-coupling-ff'} <= seen:
        raise Miss('EXTRACT_DEFAULTS_UNQUALIFIED')
    if tool is None:
        if not {'--threads', '--extract-query-workers', '--extract-query-batch-runs',
                '--extract-query-scratch-mib'} <= seen:
            raise Miss('EXTRACT_DEFAULTS_UNQUALIFIED')
    else:
        argument_profile(argv, tool)
    result = list(argv)
    for option, marker in [('--workspace', '@WORKSPACE@'), ('--out', '@OUTPUT@')]:
        index = result.index(option) + 1
        need(index < len(result) and Path(result[index]).is_absolute(), 'EXTRACT_PATH')
        result[index] = marker
    return result


def extraction_environment(threads):
    need(type(threads) is int and 1 <= threads <= 64, 'EXTRACT_THREADS_INTEGER')
    allowed = {'RUSTROAD_ROUTE_TRACE', 'RUSTROAD_RULE_DUMP'}
    if any(key.startswith('RUSTROAD_') and key not in allowed for key in os.environ):
        raise Miss('EXTRACT_ENVIRONMENT_UNQUALIFIED')
    need(not os.environ.get('RUSTROAD_RULE_DUMP'), 'EXTERNAL_DIAGNOSTIC_WRITER')
    return dict(RAYON_NUM_THREADS=str(threads),
                **{key: os.environ.get(key) for key in ('RUSTROAD_ROUTE_TRACE', 'LC_ALL', 'LANG', 'TZ')})


def selected_capture_request(capture, source, post, tool):
    """Bind the selected actual argv to the captured actual controller input."""
    need(source['schema'] == 'rusteda.native-postroute-reuse-inputs.v1'
         and source['tools']['rustroad'] == tool, 'CAPTURE_CONTROLLER_TOOL')
    recipe = source['recipe']['extract']
    need(isinstance(recipe, list) and 1 <= len(recipe) <= 128
         and all(isinstance(item, str) for item in recipe)
         and recipe.count('@SPEF@') == 1 and recipe.count('--rc-profile') == 1,
         'CAPTURE_CONTROLLER_RECIPE')
    expected = [tool['path'], '--workspace', capture['source_workspace'],
                *[capture['output']['path'] if item == '@SPEF@' else item for item in recipe]]
    index = expected.index('--rc-profile') + 1
    need(expected[index] == post['effective_rc']['source']['path'], 'CAPTURE_SOURCE_RC_PROFILE')
    expected[index] = post['effective_rc']['effective']['path']
    need(capture['command']['argv'] == expected, 'CAPTURE_ACTUAL_RECIPE_CHANGED')
    extraction_request(expected, tool)


def validate_argument_profile(value):
    need(isinstance(value, dict) and set(value) == {'schema', 'engine', 'input_closure', 'cli_threads',
        'cli_threads_origin', 'queries', 'other_omitted_defaults'}
        and value['schema'] == 'rusteda.qualified-extract-argument-profile.v1', 'EXTRACT_ARGUMENT_PROFILE_SCHEMA')
    engine = value['engine']
    need(isinstance(engine, dict) and set(engine) == {'sha256', 'bytes'}
         and type(engine['bytes']) is int and engine['bytes'] >= 0
         and isinstance(engine['sha256'], str) and re.fullmatch('[0-9a-f]{64}', engine['sha256'])
         and type(value['cli_threads']) is int and 1 <= value['cli_threads'] <= 64,
         'EXTRACT_ARGUMENT_PROFILE_COUNTER')
    defaults = value['other_omitted_defaults']
    need(isinstance(defaults, dict) and set(defaults) <= set(BOUND_DEFAULTS), 'EXTRACT_ARGUMENT_DEFAULTS')
    for name, item in defaults.items():
        need(type(item) is type(BOUND_DEFAULTS[name]), 'EXTRACT_ARGUMENT_DEFAULT_TYPE')


def reuse_match(captured, current, argv, tool, profile, environment):
    """No false process/CLI receipt: return only a strict cache eligibility bit."""
    need(captured['schema'] == 'rusteda.actual-full-extract-inputs.v1', 'EXTRACT_CAPTURE_SCHEMA')
    validate_snapshot(captured['snapshot'])
    validate_snapshot(current)
    validate_argument_profile(captured['argument_profile'])
    for value in (captured['tool'], tool, captured['profile'], profile):
        file_id_shape(value)
    need(type(captured['command']['exit_code']) is int and captured['command']['exit_code'] == 0,
         'EXTRACT_CAPTURE_FAILED')
    return (captured['snapshot'] == current and captured['tool'] == tool
            and captured['profile'] == profile and captured['environment'] == environment
            and captured['argument_profile'] == argument_profile(captured['command']['argv'], captured['tool'])
            and captured['argument_profile'] == argument_profile(argv, tool)
            and extraction_request(captured['command']['argv'], captured['tool']) == extraction_request(argv, tool))
