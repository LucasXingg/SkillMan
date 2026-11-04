import contextlib
import io
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import tomllib
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

    def test_init_default_backlinks_and_existing_config_preserved(self):
        self.root = self.base / 'fresh'
        self.assertEqual(self.run_cli('init'), 0)
        config = self.root / '.skillman/config.toml'
        self.assertTrue(all(target['disable_default_backlink'] is False
                            for target in tomllib.loads(config.read_text())['targets'].values()))
        before = self.snapshot()
        self.assertEqual(self.run_cli('init'), 0)
        self.assertEqual(before, self.snapshot())

    def test_collect_default_backlinks_disabled_preserves_existing_switches(self):
        self.configure()
        with (self.root / '.skillman/config.toml').open('a') as stream:
            stream.write('disable_default_backlink = true\n')
        original = self.skill(self.target)
        existing = self.skill(self.source, name='existing')
        (existing / '.skillman').write_text('test = true\n')
        before = self.snapshot()
        self.assertEqual(self.run_cli('collect', '--from', str(self.target), '--dry-run'), 0)
        self.assertEqual(before, self.snapshot())
        self.assertEqual(self.run_cli('collect', '--from', str(self.target)), 0)
        config = self.root / 'skills/example/.skillman'
        self.assertEqual(tomllib.loads(config.read_text()), {'test': False})
        self.assertEqual((self.root / 'skills/existing/.skillman').read_text(), 'test = true\n')
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertFalse(os.path.lexists(original))
        self.assertTrue((self.target / 'existing').is_symlink())
        self.assertEqual(self.run_cli('deploy', 'example', '--target', 'test'), 0)
        self.assertEqual(tomllib.loads(config.read_text()), {'test': True})
        self.assertTrue(original.is_symlink())

    def test_invalid_default_backlinks_rejected_before_collection(self):
        self.configure()
        with (self.root / '.skillman/config.toml').open('a') as stream:
            stream.write('disable_default_backlink = "true"\n')
        self.skill(self.source)
        before = self.snapshot()
        self.assertEqual(self.run_cli('collect'), 1)
        self.assertEqual(before, self.snapshot())

    def test_collect_target_defaults_are_independent(self):
        second = self.base / 'second'
        with (self.root / '.skillman/config.toml').open('a') as stream:
            stream.write('disable_default_backlink = true\n'
                         f'[targets.second]\npath = "{second}"\n'
                         'disable_default_backlink = false\n')
        self.skill(self.source)
        self.assertEqual(self.run_cli('collect'), 0)
        config = self.root / 'skills/example/.skillman'
        self.assertEqual(tomllib.loads(config.read_text()), {'test': False, 'second': True})
        self.assertEqual(self.run_cli('deploy'), 0)
        self.assertFalse(self.target.exists())
        self.assertTrue((second / 'example').is_symlink())

    def test_new_interactive_template_and_deployment(self):
        with patch('builtins.input', side_effect=['code-review', '检查代码：在审查时使用', '先阅读修改，再给出建议']):
            self.assertEqual(self.run_cli('new'), 0)
        skill = self.root / 'skills/code-review'
        content = (skill / 'SKILL.md').read_text()
        self.assertIn('name: "code-review"', content)
        self.assertIn('description: "检查代码：在审查时使用"', content)
        self.assertIn('先阅读修改，再给出建议', content)
        self.assertEqual(tomllib.loads((skill / '.skillman').read_text()), {'test': True})
        self.assertEqual(self.run_cli('deploy', 'code-review'), 0)
        self.assertTrue((self.target / 'code-review').is_symlink())

    def test_new_arguments_dry_run_and_disabled_defaults(self):
        self.configure()
        with (self.root / '.skillman/config.toml').open('a') as stream:
            stream.write('disable_default_backlink = true\n')
        args = ('new', 'review', '--description', 'Review "code": safely\nWhen asked.', '--no-interactive')
        before = self.snapshot()
        with patch('builtins.input', side_effect=AssertionError('Unexpected prompt')):
            self.assertEqual(self.run_cli(*args, '--dry-run'), 0)
            self.assertEqual(before, self.snapshot())
            self.assertEqual(self.run_cli(*args), 0)
        skill = self.root / 'skills/review'
        self.assertIn('TODO:', (skill / 'SKILL.md').read_text())
        self.assertIn('Review \\"code\\": safely\\nWhen asked.', (skill / 'SKILL.md').read_text())
        self.assertEqual(tomllib.loads((skill / '.skillman').read_text()), {'test': False})

    def test_new_rejects_unsafe_names_missing_fields_and_existing_paths(self):
        existing = self.skill(self.root / 'skills', name='existing')
        (self.root / 'skills/foreign').symlink_to('/missing/foreign')
        before = self.snapshot()
        for name in ('../outside', '/tmp/outside', '.', 'has space', 'UPPER', 'x' * 65,
                     'existing', 'foreign'):
            with self.subTest(name=name):
                self.assertEqual(self.run_cli('new', name, '--description', 'Test', '--no-interactive'), 1)
        self.assertEqual(self.run_cli('new', '--no-interactive'), 1)
        self.assertEqual(self.run_cli('new', 'missing-description', '--no-interactive'), 1)
        self.assertEqual(before, self.snapshot())
        self.assertEqual((existing / 'SKILL.md').read_text(), 'hello')

    def test_new_cancel_and_write_failure_leave_no_partial_skill(self):
        before = self.snapshot()
        for interruption in (EOFError, KeyboardInterrupt):
            with patch('builtins.input', side_effect=['review', interruption()]):
                self.assertEqual(self.run_cli('new'), 1)
            self.assertEqual(before, self.snapshot())
        with patch('skillman.cli.Manager.create_skill_config', side_effect=OSError('write failed')):
            self.assertEqual(self.run_cli('new', 'review', '--description', 'Test', '--no-interactive'), 1)
        self.assertEqual(before, self.snapshot())

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
