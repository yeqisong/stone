"""Agent 图表链路单测——工作区图片差分 + 文件端点路径防护。"""
import unittest

from app.agent import runtime, sandbox
from app.api import agent as api_agent


class TestWorkspaceImages(unittest.TestCase):
    SID = 990002

    def setUp(self):
        import shutil
        from pathlib import Path
        ws = sandbox.workspace(self.SID)
        for f in ('a.png', 'b.svg'):
            (ws / f).write_bytes(b'x')
        (ws / 'note.txt').write_text('t')
        (ws / 'pylibs').mkdir(exist_ok=True)
        (ws / 'pylibs' / 'lib.png').write_bytes(b'x')

    def tearDown(self):
        sandbox.reset_workspace(self.SID)

    def test_only_images_and_skip_deps(self):
        imgs = runtime.workspace_images(self.SID)
        self.assertIn('a.png', imgs)
        self.assertIn('b.svg', imgs)
        self.assertNotIn('note.txt', imgs)
        self.assertNotIn('pylibs/lib.png', imgs)

    def test_new_or_changed(self):
        before = runtime.workspace_images(self.SID)
        ws = sandbox.workspace(self.SID)
        (ws / 'c.png').write_bytes(b'y')
        after = runtime.workspace_images(self.SID)
        self.assertEqual(runtime._new_or_changed(before, after), ['c.png'])
        self.assertEqual(runtime._new_or_changed(after, after), [])


class TestSafeWorkspaceFile(unittest.TestCase):
    SID = 990003

    def setUp(self):
        ws = sandbox.workspace(self.SID)
        (ws / 'ok.png').write_bytes(b'x')
        (ws / 'secret.txt').write_text('t')
        (ws / 'sub').mkdir(exist_ok=True)
        (ws / 'sub' / 'deep.png').write_bytes(b'x')

    def tearDown(self):
        sandbox.reset_workspace(self.SID)

    def test_allow_image(self):
        self.assertIsNotNone(api_agent._safe_workspace_file(self.SID, 'ok.png'))
        self.assertIsNotNone(api_agent._safe_workspace_file(self.SID, 'sub/deep.png'))

    def test_reject_non_image(self):
        self.assertIsNone(api_agent._safe_workspace_file(self.SID, 'secret.txt'))

    def test_reject_traversal(self):
        self.assertIsNone(api_agent._safe_workspace_file(self.SID, '../main.py'))
        self.assertIsNone(api_agent._safe_workspace_file(self.SID, 'sub/../../x.png'))

    def test_reject_weird_chars(self):
        self.assertIsNone(api_agent._safe_workspace_file(self.SID, 'a b.png'))
        self.assertIsNone(api_agent._safe_workspace_file(self.SID, 'x.png;rm'))


if __name__ == '__main__':
    unittest.main()
