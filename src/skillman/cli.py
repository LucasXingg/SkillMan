"""Offline skill collection and deployment using only the standard library."""
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib

DEFAULT_CONFIG = '''sources = ["~/.codex/skills", "~/.claude/skills", "~/.agents/skills"]
exclude = [".system", ".git", "node_modules", "cache", ".cache", "plugins"]
[selection]
[targets.codex]
path = "~/.codex/skills"
enabled = true
[targets.claude]
path = "~/.claude/skills"
enabled = true
[targets.agents]
path = "~/.agents/skills"
enabled = true
'''


def atomic_json(path, value):
    fd, name = tempfile.mkstemp(dir=path.parent, prefix='.state-')
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream, indent=2, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def digest(folder):
    """Hash names, node kinds, permissions and bytes without following links."""
    result = hashlib.sha256()
    def visit(path, relative):
        mode = path.lstat().st_mode & 0o777
        kind = 'link' if path.is_symlink() else 'dir' if path.is_dir() else 'file'
        result.update(json.dumps([relative, kind, mode]).encode())
        if kind == 'link':
            result.update(os.readlink(path).encode())
        elif kind == 'dir':
            for child in sorted(path.iterdir()):
                if relative == '' and child.name == '.skillman':
                    continue
                visit(child, relative + '/' + child.name)
        elif path.is_file():
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    result.update(block)
        else:
            raise ValueError(f'Unsupported special file: {path}')
    visit(folder, '')
    return result.hexdigest()


class Manager:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.meta = self.root / '.skillman'
        self.skills = self.root / 'skills'
        self.state_path = self.meta / 'state.json'
        self.state = {'version': 1, 'origins': {}, 'links': {}, 'groups': {}}

    def path(self, value):
        p = Path(value).expanduser()
        return Path(os.path.abspath(p if p.is_absolute() else self.root / p))

    def init(self):
        self.meta.mkdir(parents=True, exist_ok=True)
        self.skills.mkdir(exist_ok=True)
        config = self.meta / 'config.toml'
        if not config.exists():
            config.write_text(DEFAULT_CONFIG)
        if not self.state_path.exists():
            self.save()
        print(f'Initialized: {self.root}')

    @contextmanager
    def locked(self, dry=False):
        if not (self.meta / 'config.toml').is_file():
            raise ValueError('Run skillman init first.')
        with (self.meta / 'config.toml').open('r') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('Another SkillMan command is running.') from None
            self.config = tomllib.loads((self.meta / 'config.toml').read_text())
            if self.state_path.exists():
                self.state = json.loads(self.state_path.read_text())
            self.state.setdefault('groups', {})
            self.recover(persist=not dry)
            yield

    def save(self):
        atomic_json(self.state_path, self.state)

    def recover(self, persist=True):
        # A journal bridges directory operations and state writes. Never delete
        # a remaining source during recovery: let the next collect verify it.
        pending = self.state.get('pending')
        if not pending:
            return
        destination = self.skills / pending['skill']
        if pending['kind'] == 'collect' and destination.is_dir():
            self.state['origins'][pending['source']] = pending['skill']
            self.state['groups'][pending['skill']] = pending.get('group', pending['skill'])
        elif pending['kind'] == 'link':
            path = Path(pending['path'])
            if path.is_symlink() and os.readlink(path) == pending['target']:
                self.state['links'][str(path)] = pending['target']
        self.state.pop('pending', None)
        if persist:
            self.save()

    def inventory(self):
        return {p.name: p for p in sorted(self.skills.iterdir())
                if p.is_dir() and not p.is_symlink() and (p / 'SKILL.md').is_file()}

    def discover(self, sources):
        excluded = set(self.config.get('exclude', []))
        seen = set()
        def walk(folder):
            if folder.is_symlink() or not folder.is_dir():
                return
            resolved = folder.resolve()
            if resolved == self.root or self.root in resolved.parents or resolved in seen:
                return
            seen.add(resolved)
            if folder.name in excluded:
                return
            if (folder / 'SKILL.md').is_file():
                if resolved in self.root.parents:
                    return
                yield folder
                return
            for child in sorted(folder.iterdir()):
                if child.is_dir() and not child.is_symlink():
                    yield from walk(child)
        for source in sources:
            yield from walk(self.path(source))

    def skill_targets(self, stored):
        path = self.skills / stored / '.skillman'
        if not path.exists():
            return {name: True for name in self.config.get('targets', {})}
        data = tomllib.loads(path.read_text())
        if any(type(value) is not bool for value in data.values()):
            raise ValueError(f'{path}: target values must be true or false')
        return data

    def enable_skill_targets(self, stored, targets, dry):
        data = self.skill_targets(stored)
        data.update({name: True for name in targets})
        print(f'CONFIG {stored}: enable {", ".join(targets)}')
        if dry:
            return
        path = self.skills / stored / '.skillman'
        if path.is_symlink():
            raise ValueError(f'Refusing to replace symlink config: {path}')
        fd, temporary = tempfile.mkstemp(prefix='.skillman-', dir=path.parent)
        try:
            with os.fdopen(fd, 'w') as stream:
                stream.write('# Distribution targets (TOML)\n')
                for name, value in data.items():
                    stream.write(f'{json.dumps(name)} = {str(value).lower()}\n')
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def create_skill_config(self, destination):
        path = destination / '.skillman'
        if not os.path.lexists(path):
            text = '# Distribution targets (TOML)\n'
            text += ''.join(f'{json.dumps(name)} = true\n'
                            for name in self.config.get('targets', {}))
            with path.open('x') as stream:
                stream.write(text)

    def collect(self, extra, dry):
        sources = list(self.config.get('sources', [])) + extra
        inventory = self.inventory()
        hashes = {name: digest(path) for name, path in inventory.items()}
        plan = []
        for source in self.discover(sources):
            fingerprint = digest(source)
            name = source.name
            group = name
            if name in hashes and hashes[name] != fingerprint:
                # Keep the original name as the conflict group; do not overwrite.
                match = next((n for n, h in hashes.items()
                              if self.state['groups'].get(n, n) == group and h == fingerprint), None)
                name = match or f'{name}--{fingerprint[:10]}'
                suffix = 2
                base = name
                while name in hashes and hashes[name] != fingerprint:
                    name = f'{base}-{suffix}'
                    suffix += 1
            if (self.skills / name).exists() and name not in hashes:
                raise ValueError(f'Destination is not a valid skill: {self.skills / name}')
            hashes[name] = fingerprint
            self.state['groups'][name] = group
            plan.append((source, name, fingerprint))
        for source, name, _ in plan:
            print(f'MOVE {source} -> {self.skills / name}')
        if dry:
            return 0
        for source, name, fingerprint in plan:
            destination = self.skills / name
            self.state['pending'] = {'kind': 'collect', 'source': str(source), 'skill': name,
                                     'group': self.state['groups'][name]}
            self.save()
            if not destination.exists():
                # Copy first even on the same filesystem; verify before removal.
                staging = Path(tempfile.mkdtemp(prefix='collect-', dir=self.meta))
                try:
                    copy = staging / 'skill'
                    shutil.copytree(source, copy, symlinks=True)
                    if digest(copy) != fingerprint or digest(source) != fingerprint:
                        raise ValueError(f'Source changed during collection: {source}')
                    os.rename(copy, destination)
                finally:
                    shutil.rmtree(staging)
            if digest(destination) != fingerprint or digest(source) != fingerprint:
                raise ValueError(f'Content changed; source retained: {source}')
            self.create_skill_config(destination)
            self.state['origins'][str(source)] = name
            self.save()
            shutil.rmtree(source)
            self.state.pop('pending', None)
            self.save()
        return 0

    def choices(self):
        inventory = self.inventory()
        groups = {}
        for name in inventory:
            group = self.state['groups'].get(name, name)
            groups.setdefault(group, []).append(name)
        selected, conflicts = {}, []
        choices = self.config.get('selection', {})
        for group, names in groups.items():
            choice = choices.get(group)
            if choice is not None:
                if choice not in inventory:
                    raise ValueError(f'Invalid selection for {group}: {choice}')
                selected[group] = choice
            elif len(names) == 1:
                selected[group] = names[0]
            else:
                conflicts.append(group)
        for group, choice in choices.items():
            if choice not in inventory:
                raise ValueError(f'Invalid selection for {group}: {choice}')
            selected[group] = choice
        return selected, conflicts

    def deploy(self, targets, dry, skill=None):
        if skill is not None:
            if skill not in self.inventory():
                raise ValueError(f'Unknown skill: {skill}')
            group = self.state['groups'].get(skill, skill)
            selected, conflicts = {group: skill}, []
        else:
            selected, conflicts = self.choices()
        for group in conflicts:
            print(f'CONFLICT {group}: rename versions or configure [selection]')
        configured = self.config.get('targets', {})
        if targets and any(t not in configured for t in targets):
            raise ValueError('Unknown target: ' + ', '.join(t for t in targets if t not in configured))
        desired = {}
        # Explicit target overrides per-skill switches only for single deployment.
        override = skill is not None and bool(targets)
        if override:
            self.enable_skill_targets(skill, targets, dry)
        switches = {stored: self.skill_targets(stored) for stored in set(selected.values())} if not override else {}
        active = {name: target for name, target in configured.items()
                  if (name in targets if targets else target.get('enabled', True))}
        for name, target in active.items():
            folder = self.path(target['path'])
            subset = target.get('skills')
            for group, stored in selected.items():
                if not override and (not switches[stored].get(name, False)
                                     or (subset is not None and group not in subset)):
                    continue
                desired[folder / group] = self.skills / stored
        # Origins inside a configured target obey exactly the same filtering.
        # Extra collection paths are restored on an unscoped deployment only.
        for original, stored in self.state['origins'].items():
            group = self.state['groups'].get(stored, stored)
            chosen = selected.get(group)
            if not chosen:
                continue
            path = Path(original)
            matching = [name for name, target in configured.items()
                        if self.path(target['path']) == path.parent]
            if matching:
                allowed = any(name in active and (override or (
                    switches[chosen].get(name, False) and (
                        configured[name].get('skills') is None
                        or group in configured[name]['skills']))) for name in matching)
            else:
                allowed = not targets
            if allowed:
                value = self.skills / chosen
                if path in desired and desired[path] != value:
                    raise ValueError(f'Conflicting destinations: {path}')
                desired[path] = value
        errors = len(conflicts)
        # Reconcile only links in this invocation's scope. A full deployment
        # includes disabled/removed targets and missing skills; conflicts are kept.
        for original, recorded in list(self.state['links'].items()):
            path = Path(original)
            if path in desired:
                continue
            stored = Path(recorded).name
            group = self.state['groups'].get(stored, stored)
            if group in conflicts:
                continue
            if skill is not None and stored != skill and group not in selected:
                continue
            if targets and not any(self.path(configured[name]['path']) == path.parent
                                   for name in targets):
                continue
            if path.is_symlink() and os.readlink(path) == recorded:
                print(f'UNLINK {path}')
                if not dry:
                    path.unlink()
                    del self.state['links'][original]
                    self.save()
            elif os.path.lexists(path):
                print(f'BLOCKED changed managed path: {path}')
                errors += 1
            elif not dry:
                del self.state['links'][original]
                self.save()
        for path, destination in desired.items():
            if path == destination or destination in path.parents or path in destination.parents:
                print(f'BLOCKED unsafe destination: {path}')
                errors += 1
                continue
            target = str(destination)
            if path.is_symlink() and os.readlink(path) == target:
                print(f'OK {path}')
                continue
            exists = os.path.lexists(path)
            owned = path.is_symlink() and self.state['links'].get(str(path)) == os.readlink(path)
            if exists and not owned:
                print(f'BLOCKED existing path: {path}')
                errors += 1
                continue
            print(f'LINK {path} -> {target}')
            if dry:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            self.state['pending'] = {'kind': 'link', 'skill': destination.name,
                                     'path': str(path), 'target': target}
            self.save()
            if owned:
                # Atomic replacement leaves the old link intact on failure.
                fd, temporary = tempfile.mkstemp(prefix='.skillman-', dir=path.parent)
                os.close(fd)
                os.unlink(temporary)
                try:
                    os.symlink(target, temporary)
                    os.replace(temporary, path)
                finally:
                    if os.path.lexists(temporary):
                        os.unlink(temporary)
            else:
                os.symlink(target, path)
            self.state['links'][str(path)] = target
            self.state.pop('pending', None)
            self.save()
        return 1 if errors else 0

    def status(self):
        selected, conflicts = self.choices()
        for group in conflicts:
            print(f'CONFLICT {group}')
        for name in self.inventory():
            print(f'SKILL {name}')
        for original, stored in self.state['origins'].items():
            effective = selected.get(self.state['groups'].get(stored, stored), stored)
            if not (self.skills / effective).is_dir():
                print(f'MISSING SOURCE {stored} (original: {original})')
            if not Path(original).is_symlink():
                print(f'PENDING original: {original}')
        for path, target in self.state['links'].items():
            p = Path(path)
            if not p.is_symlink() or os.readlink(p) != target:
                print(f'CHANGED LINK {path}')
            elif not p.exists():
                print(f'BROKEN LINK {path}')
        print('Deployment preview:')
        return self.deploy([], True)


def main(argv=None):
    parser = argparse.ArgumentParser(prog='skillman', description='Manage local agent skills, offline.')
    parser.add_argument('--root', type=Path, default=Path.cwd())
    commands = parser.add_subparsers(dest='command', required=True)
    for command in ('init', 'open', 'status'):
        commands.add_parser(command)
    collect = commands.add_parser('collect')
    collect.add_argument('--from', dest='sources', action='append', default=[])
    collect.add_argument('--dry-run', action='store_true')
    deploy = commands.add_parser('deploy')
    deploy.add_argument('skill', nargs='?', help='Deploy only this stored skill directory')
    deploy.add_argument('--target', action='append', default=[])
    deploy.add_argument('--dry-run', action='store_true')
    args = parser.parse_args(argv)
    manager = Manager(args.root)
    try:
        if args.command == 'init':
            manager.init()
            return 0
        if args.command == 'open':
            code = shutil.which('code')
            if not code:
                print(f'Open this folder in VS Code: {manager.root}\n'
                      'Install the code command using VS Code’s command palette.')
                return 0
            return subprocess.call([code, str(manager.root)])
        with manager.locked(dry=getattr(args, 'dry_run', False) or args.command == 'status'):
            if args.command == 'collect':
                return manager.collect(args.sources, args.dry_run)
            if args.command == 'deploy':
                return manager.deploy(args.target, args.dry_run, args.skill)
            return manager.status()
    except (OSError, ValueError, KeyError) as error:
        print(f'Error: {error}', file=sys.stderr)
        return 1
