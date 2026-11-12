"""Agent 技能（Skills）——可复用的分析方法包。

与 Claude Skills 同构：一个技能 = 一个目录下的 SKILL.md，带 frontmatter
（name/description），正文是给模型看的步骤、SQL 模板、注意事项。
渐进披露：系统提示只注入「名称 + 一句话描述」，模型判断命中后调 read_skill 读全文，
避免把几十个技能全文塞进上下文。

存放位置：
- 内置技能 skills/<slug>/SKILL.md —— 随仓库版本管理，只读；
- 用户技能 data/skills/<slug>/SKILL.md —— 页面/Agent 创建，可删可改（同名覆盖内置）。
"""
import re
import shutil
import time
from pathlib import Path

from loguru import logger

ROOT = Path(__file__).resolve().parents[2]
BUILTIN_DIR = ROOT / "skills"
USER_DIR = ROOT / "data" / "skills"

MAX_BODY = 20000     # read_skill 返回正文上限
_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")


def _slug(name: str) -> str:
    """技能名 → 目录名（保留字母数字与 ._-，中文名由调用方另给 slug）。"""
    s = re.sub(r"[^a-zA-Z0-9._-]+", "-", (name or "").strip()).strip("-").lower()
    return s[:64] or "skill"


def _parse(text: str) -> dict:
    """解析 frontmatter + 正文（不依赖 pyyaml：只支持 key: value 单行）。"""
    meta, body = {}, text
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.S)
    if m:
        for line in m.group(1).splitlines():
            if ":" in line and not line.strip().startswith("#"):
                k, v = line.split(":", 1)
                meta[k.strip()] = v.strip().strip('"').strip("'")
        body = m.group(2)
    return {"meta": meta, "body": body.strip()}


def _load_dir(d: Path, builtin: bool) -> dict:
    out = {}
    if not d.exists():
        return out
    for p in sorted(d.iterdir()):
        f = p / "SKILL.md"
        if not p.is_dir() or not f.exists():
            continue
        try:
            parsed = _parse(f.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"[skills] 解析失败 {f}: {e}")
            continue
        name = p.name
        first_line = parsed["body"].splitlines()[0][:80] if parsed["body"] else ""
        desc = parsed["meta"].get("description") or first_line
        out[name] = {
            "name": name,
            "display_name": parsed["meta"].get("title") or name,
            "description": desc,
            "builtin": builtin,
            "path": str(p),
            "size": len(parsed["body"]),
            "files": sorted(x.name for x in p.iterdir() if x.is_file() and x.name != "SKILL.md"),
        }
    return out


def all_skills() -> dict:
    """内置 + 用户（同名用户覆盖内置）。"""
    merged = _load_dir(BUILTIN_DIR, True)
    merged.update(_load_dir(USER_DIR, False))
    return merged


def list_skills() -> list:
    return sorted(all_skills().values(), key=lambda s: (not s["builtin"], s["name"]))


def read_skill(name: str, file: str = "") -> dict:
    """读技能正文（或技能目录下的附加文件）。"""
    sk = all_skills().get((name or "").strip())
    if not sk:
        return {"error": f"技能不存在: {name}", "available": [s["name"] for s in list_skills()]}
    base = Path(sk["path"]).resolve()
    target = base / "SKILL.md" if not file else (base / file).resolve()
    # 防目录穿越
    if base not in target.parents and target != base / "SKILL.md":
        return {"error": "非法文件路径"}
    if not target.exists() or not target.is_file():
        return {"error": f"文件不存在: {file}", "files": sk["files"]}
    text = target.read_text(encoding="utf-8", errors="replace")
    body = text if file else _parse(text)["body"]
    truncated = len(body) > MAX_BODY
    return {"name": sk["name"], "display_name": sk["display_name"], "builtin": sk["builtin"],
            "file": file or "SKILL.md",
            "content": body[:MAX_BODY], "truncated": truncated,
            "files": sk["files"]}


def save_skill(name: str, description: str, content: str, title: str = "") -> dict:
    """创建/覆盖用户技能（不碰内置技能）。name 为目录名（英文/数字/-_.）。"""
    slug = (name or "").strip()
    if not _NAME_RE.match(slug):
        return {"error": "技能名只能用字母/数字/._-（建议英文短横线命名，如 stock-deep-dive）"}
    if (BUILTIN_DIR / slug).exists():
        return {"error": f"「{slug}」是内置技能目录名，请换一个名字"}
    desc = (description or "").strip()
    if len(desc) < 4:
        return {"error": "description 至少 4 个字符（模型靠它判断何时使用该技能）"}
    if not (content or "").strip():
        return {"error": "技能正文不能为空"}
    d = USER_DIR / slug
    d.mkdir(parents=True, exist_ok=True)
    fm = ["---", f"name: {slug}", f"description: {desc}"]
    if title:
        fm.append(f"title: {title.strip()}")
    fm.append("---")
    (d / "SKILL.md").write_text("\n".join(fm) + "\n\n" + content.strip() + "\n", encoding="utf-8")
    logger.info(f"[skills] 已保存用户技能 {slug}")
    return {"ok": True, "name": slug, "description": desc}


def delete_skill(name: str) -> dict:
    slug = (name or "").strip()
    sk = all_skills().get(slug)
    if not sk:
        return {"error": "技能不存在"}
    if sk["builtin"]:
        return {"error": "内置技能随仓库维护、不可删除（要定制请用新名字另建技能）"}
    shutil.rmtree(Path(sk["path"]), ignore_errors=True)
    return {"ok": True}


# ── GitHub 技能导入（预览 → 人工确认入库；不自动安装）──────────

_IMPORT_RE = re.compile(
    r"^https://github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(\.git)?/?$")
_TMP = ROOT / "data" / "skills" / ".import-tmp"
_IMPORT_CACHE = {}          # repo_url -> (path, repo, ts)：预览后确认导入不再 clone 一次
_CACHE_TTL = 600


def _clone_shallow(repo_url: str) -> tuple:
    """浅克隆到临时目录，返回 (目录, 仓库默认 slug)。"""
    import subprocess
    m = _IMPORT_RE.match((repo_url or "").strip())
    if not m:
        raise ValueError("仅支持 github.com 仓库 URL（https://github.com/<owner>/<repo>）")
    owner, repo = m.group(1), m.group(2)
    tmp = _TMP / f"{owner}__{repo}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    _TMP.mkdir(parents=True, exist_ok=True)
    r = subprocess.run(
        ["git", "clone", "--depth", "1", "--single-branch",
         f"https://github.com/{owner}/{repo}.git", str(tmp)],
        capture_output=True, timeout=180)
    if r.returncode != 0:
        raise ValueError(f"克隆失败: {r.stderr.decode('utf-8', 'replace')[:200]}")
    shutil.rmtree(tmp / ".git", ignore_errors=True)
    return tmp, repo


def _clone_cached(repo_url: str) -> tuple:
    """预览→确认共用一次浅克隆（10 分钟内有效），避免二次拉取。"""
    key = (repo_url or "").strip()
    hit = _IMPORT_CACHE.get(key)
    if hit and hit[0].exists() and time.time() - hit[2] < _CACHE_TTL:
        return hit[0], hit[1]
    path, repo = _clone_shallow(key)
    _IMPORT_CACHE[key] = (path, repo, time.time())
    return path, repo


def _find_skill_md(d: Path) -> Path:
    """根目录 SKILL.md 优先；其次 skills/<x>/SKILL.md（唯一时）。"""
    if (d / "SKILL.md").exists():
        return d / "SKILL.md"
    subs = [p for p in d.glob("skills/*/SKILL.md")]
    if len(subs) == 1:
        return subs[0]
    if len(subs) > 1:
        raise ValueError("仓库含多个技能（skills/*/SKILL.md），请手动选择子目录安装")
    raise ValueError("未找到 SKILL.md（根目录或 skills/*/ 下）")


def import_github_preview(repo_url: str) -> dict:
    """拉取并预览：不落库，只返回技能元信息与正文预览。"""
    try:
        tmp, repo = _clone_cached(repo_url)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        f = _find_skill_md(tmp)
        parsed = _parse(f.read_text(encoding="utf-8", errors="replace"))
        files = sorted(str(p.relative_to(tmp)) for p in tmp.rglob("*")
                       if p.is_file() and ".git" not in p.parts)
        size = sum(p.stat().st_size for p in tmp.rglob("*") if p.is_file())
        return {
            "repo": repo,
            "default_slug": parsed["meta"].get("name") or repo,
            "title": parsed["meta"].get("title") or repo,
            "description": parsed["meta"].get("description") or "",
            "body_preview": parsed["body"][:600],
            "body_chars": len(parsed["body"]),
            "file_count": len(files),
            "files": files[:40],
            "size_mb": round(size / 1024 / 1024, 2),
        }
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}


def import_github_confirm(repo_url: str, slug: str = "") -> dict:
    """确认入库：移动临时目录到 data/skills/<slug>（优先复用预览的克隆）。"""
    if not _NAME_RE.match((slug or "").strip() or "x"):
        pass  # slug 缺省时用 repo 名再校验
    try:
        tmp, repo = _clone_cached(repo_url)
    except ValueError as e:
        return {"error": str(e)}
    except Exception as e:
        return {"error": f"{type(e).__name__}: {str(e)[:200]}"}
    try:
        meta_name = _parse((tmp / "SKILL.md").read_text(encoding="utf-8", errors="replace"))["meta"].get("name") or ""
    except Exception:
        meta_name = ""
    use = (slug or "").strip() or meta_name or repo
    if not _NAME_RE.match(use):
        shutil.rmtree(tmp, ignore_errors=True)
        return {"error": "技能名只能用字母/数字/._-"}
    if (BUILTIN_DIR / use).exists():
        shutil.rmtree(tmp, ignore_errors=True)
        return {"error": f"「{use}」是内置技能目录名，请换一个"}
    try:
        _find_skill_md(tmp)          # 确认存在再落位
    except ValueError as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return {"error": str(e)}
    dest = USER_DIR / use
    if dest.exists():
        shutil.rmtree(dest, ignore_errors=True)
    dest.mkdir(parents=True, exist_ok=True)
    for item in tmp.iterdir():
        shutil.move(str(item), str(dest / item.name))
    shutil.rmtree(tmp, ignore_errors=True)
    _IMPORT_CACHE.pop((repo_url or "").strip(), None)
    logger.info(f"[skills] 已从 GitHub 导入技能 {use}")
    return {"ok": True, "name": use}


def skill_index_text(limit: int = 120) -> str:
    """系统提示用的技能清单（名称 + 描述）。超过上限时明确提示还有未列出的技能。"""
    all_items = list_skills()
    items = all_items[:limit]
    if not items:
        return "（暂无技能）"
    body = "\n".join(f"- {s['name']}：{s['description']}" + ("（内置）" if s["builtin"] else "")
                     for s in items)
    if len(all_items) > len(items):
        body += f"\n（另有 {len(all_items) - len(items)} 个技能未列出，可用 list_skills 查看）"
    return body
