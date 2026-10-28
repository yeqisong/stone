"""Agent 技能（Skills）——可复用的分析方法包。

与 Claude Skills 同构：一个技能 = 一个目录下的 SKILL.md，带 frontmatter
（name/description），正文是给模型看的步骤、SQL 模板、注意事项。
渐进披露：系统提示只注入「名称 + 一句话描述」，模型判断命中后调 read_skill 读全文，
避免把几十个技能全文塞进上下文。

存放位置：
- 内置技能 skills/<slug>/SKILL.md —— 随仓库版本管理，只读；
- 用户技能 data/skills/<slug>/SKILL.md —— 页面/Agent 创建，可删可改（同名覆盖内置）。
"""
import json
import re
import shutil
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
        desc = parsed["meta"].get("description") or parsed["body"].splitlines()[0][:80] if parsed["body"] else ""
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


def skill_index_text(limit: int = 40) -> str:
    """系统提示用的技能清单（名称 + 描述）。"""
    items = list_skills()[:limit]
    if not items:
        return "（暂无技能）"
    return "\n".join(f"- {s['name']}：{s['description']}" + ("（内置）" if s["builtin"] else "")
                     for s in items)
