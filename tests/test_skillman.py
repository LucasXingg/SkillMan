import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from skillman.cli import Manager, main

REPO = Path(__file__).resolve().parents[1]


class Workflow(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.root = self.base / 'manager'
        self.source = self.base / 'source'
        self.target = self.base / 'agent'
        self.root.mkdir()
        self.run_cli('init')
        self.configure()

    def tearDown(self):
        self.temp.cleanup()

    def configure(self, extra=''):
        (self.root / '.skillman/config.toml').write_text(
            f'sources = ["{self.source}"]\nexclude = [".system", "cache"]\n'
            + extra + f'\n[targets.test]\npath = "{self.target}"\nenabled = true\n')

    def skill(self, parent, name='example', content='hello'):
        p = parent / name
        p.mkdir(parents=True)
        (p / 'SKILL.md').write_text(content)
        return p

    def run_cli(self, *args):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return main(['--root', str(self.root), *args])

    def snapshot(self):
        return sorted((str(p.relative_to(self.base)),
                       os.readlink(p) if p.is_symlink() else p.read_bytes() if p.is_file() else None)
                      for p in self.base.rglob('*'))

    def test_collect_edit_deploy_idempotent(self):
        source = self.skill(self.source)
        (source / '.hidden').write_text('keep')
        (source / 'alias').symlink_to('SKILL.md')
        self.assertEqual(self.run_cli('collect'), 0)
        self.assertFalse(source.exists())
        central = self.root / 'skills/example'
        self.assertEqual((central / '.hidden').read_text(), 'keep')
        self.assertTrue((central / 'alias').is_symlink())
        (central / 'SKILL.md').write_text('edited')
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertTrue(source.is_symlink())
        self.assertEqual((self.target / 'example/SKILL.md').read_text(), 'edited')
        before = self.snapshot()
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertEqual(before, self.snapshot())

    def test_dry_runs_and_status_are_read_only(self):
        self.skill(self.source)
        before = self.snapshot()
        self.assertEqual(self.run_cli('collect', '--dry-run'), 0)
        self.assertEqual(before, self.snapshot())
        self.run_cli('collect')
        before = self.snapshot()
        self.assertEqual(self.run_cli('deploy', '--dry-run'), 0)
        self.assertEqual(self.run_cli('status'), 0)
        self.assertEqual(before, self.snapshot())

    def test_duplicates_and_conflict_selection(self):
        self.skill(self.source / 'one')
        self.skill(self.source / 'two')
        self.skill(self.source / 'three', content='different')
        self.assertEqual(self.run_cli('collect'), 0)
        names = sorted(p.name for p in (self.root / 'skills').iterdir())
        self.assertEqual(len(names), 2)
        self.assertEqual(self.run_cli('deploy'), 1)
        self.assertFalse(self.target.exists())
        chosen = next(n for n in names if '--' in n)
        self.configure(f'[selection]\nexample = "{chosen}"\n')
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertEqual((self.target / 'example/SKILL.md').read_text(), 'different')

    def test_existing_target_and_foreign_link_protected(self):
        self.skill(self.source)
        self.run_cli('collect')
        existing = self.skill(self.target, content='keep')
        self.assertEqual(self.run_cli('deploy'), 1)
        self.assertEqual((existing / 'SKILL.md').read_text(), 'keep')
        shutil.rmtree(existing)
        existing.symlink_to('/missing/foreign')
        self.assertEqual(self.run_cli('deploy'), 1)
        self.assertEqual(os.readlink(existing), '/missing/foreign')

    def test_relocation_repairs_owned_links(self):
        self.skill(self.source)
        self.run_cli('collect')
        self.run_cli('deploy')
        moved = self.base / 'moved'
        self.root.rename(moved)
        self.root = moved
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertEqual((self.target / 'example/SKILL.md').read_text(), 'hello')

    def test_exclusions_and_no_symlink_recursion(self):
        self.skill(self.source / '.system')
        self.skill(self.source / 'cache')
        self.source.mkdir(exist_ok=True)
        (self.source / 'loop').symlink_to(self.source)
        self.assertEqual(self.run_cli('collect'), 0)
        self.assertEqual(list((self.root / 'skills').iterdir()), [])

    def test_copy_failure_preserves_source(self):
        source = self.skill(self.source)
        with patch('skillman.cli.shutil.copytree', side_effect=OSError('copy failed')):
            self.assertEqual(self.run_cli('collect'), 1)
        self.assertEqual((source / 'SKILL.md').read_text(), 'hello')
        self.assertEqual(self.run_cli('collect'), 0)
        self.assertEqual(self.run_cli('deploy'), 0)

    def test_renamed_skill_selection_repairs_origin(self):
        source = self.skill(self.source)
        self.run_cli('collect')
        (self.root / 'skills/example').rename(self.root / 'skills/renamed')
        self.configure('[selection]\nexample = "renamed"\n')
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertEqual((source / 'SKILL.md').read_text(), 'hello')

    def test_names_with_double_dash_are_independent(self):
        self.skill(self.source, name='my--skill')
        self.run_cli('collect')
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertTrue((self.target / 'my--skill').is_symlink())

    def test_recover_after_source_removal_failure(self):
        source = self.skill(self.source)
        real_remove = shutil.rmtree
        def remove(path, *args, **kwargs):
            if Path(path) == source:
                raise OSError('simulated removal failure')
            return real_remove(path, *args, **kwargs)
        with patch('skillman.cli.shutil.rmtree', side_effect=remove):
            self.assertEqual(self.run_cli('collect'), 1)
        self.assertTrue(source.is_dir())
        self.assertTrue((self.root / 'skills/example/SKILL.md').exists())
        before = self.snapshot()
        self.run_cli('collect', '--dry-run')
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.run_cli('collect'), 0)
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertTrue(source.is_symlink())

    def test_collect_creates_per_skill_targets_and_bulk_obeys_false(self):
        self.skill(self.source)
        self.run_cli('collect')
        config = self.root / 'skills/example/.skillman'
        self.assertIn('"test" = true', config.read_text())
        config.write_text('test = false\n')
        self.assertEqual(self.run_cli('deploy', '--target', 'test'), 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertFalse(self.target.exists())

    def test_single_skill_target_override(self):
        self.skill(self.source)
        self.skill(self.source, name='other')
        self.run_cli('collect')
        (self.root / 'skills/example/.skillman').write_text('test = false\n')
        self.assertEqual(self.run_cli('deploy', 'example'), 0)
        self.assertFalse(self.target.exists())
        self.assertEqual(self.run_cli('deploy', 'example', '--target', 'test'), 0)
        self.assertTrue((self.target / 'example').is_symlink())
        self.assertFalse((self.target / 'other').exists())
        self.assertEqual(self.run_cli('deploy', 'unknown', '--target', 'test'), 1)

    def test_origin_in_target_obeys_switch_and_target_scope(self):
        original = self.skill(self.target)
        self.run_cli('collect', '--from', str(self.target))
        (self.root / 'skills/example/.skillman').write_text('test = false\n')
        self.assertEqual(self.run_cli('deploy', '--target', 'test'), 0)
        self.assertFalse(original.exists())
        self.assertEqual(self.run_cli('deploy', 'example', '--target', 'test'), 0)
        self.assertTrue(original.is_symlink())

    def test_generated_metadata_does_not_prevent_duplicate_merge(self):
        self.skill(self.source)
        self.run_cli('collect')
        extra = self.base / 'extra'
        self.skill(extra)
        self.assertEqual(self.run_cli('collect', '--from', str(extra)), 0)
        self.assertEqual(len(list((self.root / 'skills').iterdir())), 1)

    def test_false_removes_managed_link_and_dry_run_is_read_only(self):
        self.skill(self.source)
        self.run_cli('collect')
        self.run_cli('deploy')
        config = self.root / 'skills/example/.skillman'
        config.write_text('test = false\n')
        before = self.snapshot()
        self.assertEqual(self.run_cli('deploy', '--dry-run'), 0)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertFalse(os.path.lexists(self.target / 'example'))
        self.assertEqual(self.run_cli('deploy'), 0)

    def test_single_target_persists_true_and_preserves_other_switches(self):
        import tomllib
        self.skill(self.source)
        self.run_cli('collect')
        config = self.root / 'skills/example/.skillman'
        config.write_text('test = false\nother = false\n')
        before = self.snapshot()
        self.assertEqual(self.run_cli('deploy', 'example', '--target', 'test', '--dry-run'), 0)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.run_cli('deploy', 'example', '--target', 'test'), 0)
        self.assertEqual(tomllib.loads(config.read_text()), {'test': True, 'other': False})
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertTrue((self.target / 'example').is_symlink())

    def test_scoped_cleanup_preserves_other_skills_and_targets(self):
        self.skill(self.source)
        self.skill(self.source, name='other')
        second = self.base / 'second'
        with (self.root / '.skillman/config.toml').open('a') as stream:
            stream.write(f'\n[targets.second]\npath = "{second}"\nenabled = true\n')
        self.run_cli('collect')
        self.run_cli('deploy')
        for name in ('example', 'other'):
            (self.root / 'skills' / name / '.skillman').write_text('test = false\nsecond = false\n')
        self.assertEqual(self.run_cli('deploy', '--target', 'test'), 0)
        self.assertFalse(os.path.lexists(self.target / 'example'))
        self.assertFalse(os.path.lexists(self.target / 'other'))
        self.assertTrue((second / 'example').is_symlink())
        self.assertTrue((second / 'other').is_symlink())
        self.assertEqual(self.run_cli('deploy', 'example'), 0)
        self.assertFalse(os.path.lexists(second / 'example'))
        self.assertTrue((second / 'other').is_symlink())

    def test_cleanup_preserves_replaced_link_and_real_directory(self):
        self.skill(self.source)
        self.run_cli('collect')
        self.run_cli('deploy')
        link = self.target / 'example'
        link.unlink()
        link.symlink_to('/foreign/path')
        (self.root / 'skills/example/.skillman').write_text('test = false\n')
        self.assertEqual(self.run_cli('deploy'), 1)
        self.assertEqual(os.readlink(link), '/foreign/path')
        link.unlink()
        link.mkdir()
        (link / 'precious').write_text('keep')
        self.assertEqual(self.run_cli('deploy'), 1)
        self.assertEqual((link / 'precious').read_text(), 'keep')

    def test_lock_blocks_second_writer(self):
        manager = Manager(self.root)
        with manager.locked():
            self.assertEqual(self.run_cli('collect'), 1)

    @unittest.skipUnless(shutil.which('zsh'), 'zsh required')
    def test_hook_enter_child_leave_and_resource(self):
        # Exercise the actual hook against its actual repository root.
        script = '''
cd /tmp
saved_path="$PATH"
source "$1/scripts/hook.zsh"
[[ "$PATH" == "$saved_path" ]] || exit 1
cd "$1"
[[ "$(whence -p skillman)" == "$1/bin/skillman" ]] || exit 2
cd skills
[[ "$(whence -p skillman)" == "$1/bin/skillman" ]] || exit 3
source "$1/scripts/hook.zsh"
cd /tmp
[[ "$PATH" == "$saved_path" ]] || exit 4
'''
        result = subprocess.run(['zsh', '-f', '-c', script, 'test', str(REPO)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == '__main__':
    unittest.main()
