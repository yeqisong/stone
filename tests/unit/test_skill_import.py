"""GitHub 技能导入单测——本地临时 git 仓库模拟，不依赖网络。"""
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.agent import skills as S


def _make_repo(d: Path, skill_md: str = "---\nname: demo-skill\ndescription: 单测演示技能\n---\n\n# 步骤\n1. 查库"):
    d.mkdir(parents=True, exist_ok=True)
    (d / "SKILL.md").write_text(skill_md, encoding="utf-8")
    (d / "helper.py").write_text("print('x')", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=d, check=True)
    subprocess.run(["git", "add", "."], cwd=d, check=True)
    subprocess.run(["git", "-c", "user.email=t@t", "-c", "user.name=t", "commit", "-qm", "init"], cwd=d, check=True)


class TestSkillImport(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self._old_tmp = S._TMP
        S._TMP = self.tmp / "import-tmp"

    def tearDown(self):
        S._TMP = self._old_tmp
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)
        S.delete_skill("demo-skill")
        S.delete_skill("my-demo")

    def _local_url(self, name="demo-repo"):
        d = self.tmp / name
        _make_repo(d)
        return str(d)   # git clone 支持本地路径

    def test_url_validation(self):
        r = S.import_github_preview("https://gitlab.com/a/b")
        self.assertIn("仅支持 github.com", r["error"])
        r = S.import_github_preview("not a url")
        self.assertIn("仅支持 github.com", r["error"])

    def test_preview_and_confirm(self):
        import shutil as _sh2  # noqa
        url = self._local_url("demo-repo")
        # mock 掉克隆：直接从本地目录复制
        import shutil as _sh
        src = self.tmp / "demo-repo"
        def _fake_clone(u, tmp_parent=None):
            dst = S._TMP / "owner__demo"
            if dst.exists():
                _sh.rmtree(dst, ignore_errors=True)
            S._TMP.mkdir(parents=True, exist_ok=True)
            _sh.copytree(src, dst)
            return dst, "demo"
        orig_clone = S._clone_shallow
        S._clone_shallow = _fake_clone
        try:
            pv = S.import_github_preview(url)
            self.assertNotIn("error", pv)
            self.assertEqual(pv["default_slug"], "demo-skill")
            self.assertIn("单测演示技能", pv["description"])
            self.assertGreaterEqual(pv["file_count"], 2)
            cf = S.import_github_confirm(url)
            self.assertTrue(cf.get("ok"))
            self.assertEqual(cf["name"], "demo-skill")
            # 入库后可读取
            names = [x["name"] for x in S.list_skills()]
            self.assertIn("demo-skill", names)
            # 重名覆盖（幂等）
            cf2 = S.import_github_confirm(url, slug="my-demo")
            self.assertTrue(cf2.get("ok"))
            self.assertIn("my-demo", [x["name"] for x in S.list_skills()])
        finally:
            S._clone_shallow = orig_clone

    def test_builtin_name_rejected(self):
        url = self._local_url("stock-deep-dive")
        import shutil as _sh
        def _fake_clone(u, tmp_parent=None):
            dst = S._TMP / "owner__x"
            if dst.exists():
                _sh.rmtree(dst, ignore_errors=True)
            S._TMP.mkdir(parents=True, exist_ok=True)
            _sh.copytree(url, dst)
            return dst, "x"
        orig_clone = S._clone_shallow
        S._clone_shallow = _fake_clone
        try:
            r = S.import_github_confirm(url, slug="stock-deep-dive")
            self.assertIn("内置技能目录名", r["error"])
        finally:
            S._clone_shallow = orig_clone

    def test_missing_skill_md(self):
        import shutil as _sh
        d = self.tmp / "empty-repo"
        d.mkdir()
        subprocess.run(["git", "init", "-q"], cwd=d, check=True)
        def _fake_clone(u, tmp_parent=None):
            dst = S._TMP / "owner__empty"
            if dst.exists():
                _sh.rmtree(dst, ignore_errors=True)
            S._TMP.mkdir(parents=True, exist_ok=True)
            _sh.copytree(d, dst)
            return dst, "empty"
        orig_clone = S._clone_shallow
        S._clone_shallow = _fake_clone
        try:
            r = S.import_github_preview(str(d))
            self.assertIn("未找到 SKILL.md", r["error"])
        finally:
            S._clone_shallow = orig_clone


if __name__ == "__main__":
    unittest.main()
