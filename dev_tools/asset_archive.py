"""Preview or archive asset images, preserving paths and SHA-256 checksums."""

import argparse
import hashlib
import json
import sys
from pathlib import Path
from uuid import uuid4


ROOT = Path(__file__).resolve().parents[1]
ARCHIVE = Path('.cache/unused-assets')


def file_hash(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def checked_path(root, value, parent):
    path = (root / value).resolve()
    allowed = (root / parent).resolve()
    if not allowed.is_relative_to(root) or not path.is_relative_to(allowed):
        raise ValueError(f'Path outside {parent}: {value}')
    return path


def create_archive_plan(patterns, *, root=None):
    root = (root or ROOT).resolve()
    sources = set()
    for pattern in patterns:
        normalized = str(pattern).replace('\\', '/')
        if Path(normalized).is_absolute() or '..' in Path(normalized).parts:
            raise ValueError(f'Expected a repository-relative asset pattern: {pattern}')
        matches = sorted(root.glob(normalized))
        if not matches:
            raise ValueError(f'No asset matched: {pattern}')
        for match in matches:
            source = checked_path(root, match, 'assets')
            if match.is_symlink() or not source.is_file() or source.suffix.lower() != '.png':
                raise ValueError(f'Expected an asset PNG file: {match}')
            sources.add(source)
    if not sources:
        raise ValueError('No assets selected')
    files = []
    for source in sorted(sources):
        target = checked_path(root, ARCHIVE / source.relative_to(root / 'assets'), ARCHIVE)
        if target.exists() or target.is_symlink():
            raise FileExistsError(f'Archive destination exists: {target}')
        files.append(dict(
            original_path=source.relative_to(root).as_posix(),
            archived_path=target.relative_to(root).as_posix(),
            sha256=file_hash(source), bytes=source.stat().st_size, moved=False,
        ))
    return dict(
        version=1, status='preview',
        manifest=(ARCHIVE / f'asset-archive-{uuid4().hex}.json').as_posix(),
        files=files,
    )


def verify_archive(plan, *, root=None):
    root = (root or ROOT).resolve()
    for record in plan['files']:
        target = checked_path(root, record['archived_path'], ARCHIVE)
        if not target.is_file() or file_hash(target) != record['sha256']:
            raise ValueError(f'Archived file missing or changed: {target}')
        if target.stat().st_size != record['bytes']:
            raise ValueError(f'Archived file size changed: {target}')
    return dict(version=1, status='verified', count=len(plan['files']))


def write_archive(plan, *, root=None):
    root = (root or ROOT).resolve()
    manifest = checked_path(root, plan['manifest'], ARCHIVE)
    moves = []
    sources, targets = set(), set()
    # Validate the entire batch before the first move. A stale preview or
    # collision must leave every source in place. Persist progress before
    # propagating an I/O error, so partial moves remain traceable by path.
    for record in plan['files']:
        source = checked_path(root, record['original_path'], 'assets')
        target = checked_path(root, record['archived_path'], ARCHIVE)
        if source in sources or target in targets:
            raise ValueError('Duplicate source or archive destination')
        sources.add(source)
        targets.add(target)
        if not source.is_file() or file_hash(source) != record['sha256']:
            raise ValueError(f'Source missing or changed since preview: {source}')
        if source.stat().st_size != record['bytes']:
            raise ValueError(f'Source size changed since preview: {source}')
        if target.exists() or target.is_symlink():
            raise FileExistsError(f'Archive destination exists: {target}')
        moves.append((source, target, record))
    if not moves:
        raise ValueError('No assets selected')
    plan['status'] = 'in_progress'
    manifest.parent.mkdir(parents=True, exist_ok=True)
    with manifest.open('x', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(plan, ensure_ascii=False, indent=2) + '\n')
    try:
        for source, target, record in moves:
            target.parent.mkdir(parents=True, exist_ok=True)
            # Use a same-filesystem rename; never fall back to copy + delete.
            # Recheck collisions immediately before each rename.
            if target.exists() or target.is_symlink():
                raise FileExistsError(f'Archive destination exists: {target}')
            source.rename(target)
            record['moved'] = True
            if file_hash(target) != record['sha256']:
                raise ValueError(f'Archived file changed during move: {target}')
            manifest.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n',
                                encoding='utf-8', newline='\n')
        plan['status'] = 'written'
    except (OSError, ValueError) as exc:
        plan['status'] = 'failed'
        plan['error'] = str(exc)
        raise
    finally:
        manifest.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + '\n',
                            encoding='utf-8', newline='\n')
    return plan


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    move = commands.add_parser('move', help='preview selected assets; --write moves them')
    move.add_argument('patterns', nargs='+', help='repository-relative PNG paths or glob patterns')
    move.add_argument('--write', action='store_true')
    verify = commands.add_parser('verify', help='check archived files against a saved manifest')
    verify.add_argument('manifest', help='repository-relative archive manifest path')
    args = parser.parse_args(argv)
    try:
        if args.command == 'verify':
            manifest = checked_path(ROOT, args.manifest, ARCHIVE)
            result = verify_archive(json.loads(manifest.read_text(encoding='utf-8')))
        else:
            result = create_archive_plan(args.patterns)
            if args.write:
                result = write_archive(result)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'error: {exc}', file=sys.stderr)
        return 2
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
