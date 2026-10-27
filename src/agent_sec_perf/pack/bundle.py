"""能力包（bundle）：构建、摘要与完整性校验。

一个 bundle 是**可分发的最小单位**::

    <bundle>/
      manifest.json          每个文件的 sha256 + 判据覆盖范围
      README.md              能力边界声明（人读）
      case/                  案例原件（case.toml / scene / pack* / selftest）
      evidence/              （可选）运行证据，**不参与**完整性校验

三条设计取向：

1. **确定性**：同输入重复构建，除显式 ``--stamp`` 外**字节一致**（文件按路径排序、
   JSON ``sort_keys``、不写时间戳）。否则"两次构建不一致"会被误读成"内容变了"。
2. **篡改可见**：``verify`` 同时检查"清单里的文件"和"目录里多出来的文件"——
   只查前者的话，往包里塞一个文件不会被发现。
3. **包是不可信输入**：清单里的路径必须相对、无 ``..``、经
   :func:`foundation.paths.resolve_within` 解析；符号链接一律拒绝；文件数与总体积有上限。
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final, cast

from agent_sec_perf.foundation.errors import BenchError
from agent_sec_perf.pack.case import CaseSpec, CaseSpecError, load_case_spec
from agent_sec_perf.pack.criteria import SelftestResult, run_selftest

__all__ = [
    "BuildResult",
    "BundleError",
    "FileEntry",
    "Manifest",
    "VerifyResult",
    "build_bundle",
    "verify_bundle",
]

MANIFEST_SCHEMA: Final = "capability-pack/v1"
MANIFEST_NAME: Final = "manifest.json"
CASE_DIR_NAME: Final = "case"
EVIDENCE_DIR_NAME: Final = "evidence"
README_NAME: Final = "README.md"

#: 上限：防止一个"包"把磁盘读穿（包来自他人时这是拒绝服务面）。
MAX_FILES: Final = 512
MAX_FILE_BYTES: Final = 32 * 1024 * 1024
MAX_TOTAL_BYTES: Final = 64 * 1024 * 1024
MAX_MANIFEST_BYTES: Final = 1024 * 1024
_MAX_README_BYTES: Final = 512 * 1024

_FORBIDDEN_DIRECTORY = "__pycache__"
_FORBIDDEN_SUFFIXES = (".py", ".pyc")


class BundleError(BenchError, ValueError):
    """bundle 的构建或校验失败（装配期故障 ⇒ 退出码 ``3``）。"""


@dataclass(frozen=True)
class FileEntry:
    """清单中的一个文件（路径是 bundle 内的 POSIX 相对路径）。"""

    path: str
    sha256: str
    size: int


@dataclass(frozen=True)
class Manifest:
    """``manifest.json`` 的内存表示。"""

    schema: str
    name: str
    version: str
    case_id: str
    files: tuple[FileEntry, ...]
    covered: tuple[str, ...]
    uncovered: tuple[str, ...]
    model: str
    stamped: str | None = None

    def to_json(self) -> str:
        """序列化（键排序 + 2 空格缩进 + 末尾换行 ⇒ 可 diff、可复现）。"""
        payload: dict[str, object] = {
            "schema": self.schema,
            "name": self.name,
            "version": self.version,
            "case_id": self.case_id,
            "model": self.model,
            "criteria": {"covered": list(self.covered), "uncovered": list(self.uncovered)},
            "files": [
                {"path": entry.path, "sha256": entry.sha256, "size": entry.size}
                for entry in self.files
            ],
        }
        if self.stamped is not None:
            payload["stamped"] = self.stamped
        return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


@dataclass(frozen=True)
class BuildResult:
    """一次构建的结果（供 CLI 渲染与测试断言）。"""

    bundle_dir: Path
    manifest: Manifest
    selftest: SelftestResult


@dataclass(frozen=True)
class VerifyResult:
    """一次校验的结果；``problems`` 为空且 ``selftest.ok`` 才算通过。"""

    bundle_dir: Path
    manifest: Manifest | None
    problems: tuple[str, ...]
    selftest: SelftestResult | None

    @property
    def ok(self) -> bool:
        """完整性无问题且判据自证通过。"""
        return not self.problems and self.selftest is not None and self.selftest.ok


def build_bundle(
    case_dir: Path,
    out_dir: Path,
    *,
    version: str = "0.1.0",
    stamp: str | None = None,
    force: bool = False,
) -> BuildResult:
    """把案例目录编译成一个 bundle。

    Args:
        case_dir: 案例目录（含 ``case.toml``）。
        out_dir: 输出目录。**已存在且非空 ⇒ 拒绝**（``force=True`` 才覆盖），
            避免"上一次的残留文件被当成这一次的产物"。
        version: 包版本（写入 manifest，不由构建推导）。
        stamp: 可选的构建标记（如提交哈希）。``None`` ⇒ 不写，保证确定性。
        force: 允许覆盖已存在的非空目录。

    Raises:
        CaseSpecError: 案例非法或判据无法自证。
        BundleError: 输出目录不可用、案例含代码/符号链接、超出上限。
    """
    case = load_case_spec(case_dir)
    selftest = run_selftest(case)
    if not selftest.ok:
        raise BundleError("判据自证未通过：正例未全过或反例未失败，拒绝发布")

    _prepare_output(out_dir, force=force)
    target_case = out_dir / CASE_DIR_NAME
    files: list[FileEntry] = []

    for source in _walk_files(case.case_dir):
        relative = source.relative_to(case.case_dir).as_posix()
        if relative.startswith("."):
            # 隐藏文件（编辑器/版本控制残留）不进包：它们既不是现场也不是判据。
            continue
        destination = target_case / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, destination)
        files.append(_entry_for(destination, relative=f"{CASE_DIR_NAME}/{relative}"))

    readme = out_dir / README_NAME
    readme.write_text(_render_readme(case=case, selftest=selftest), encoding="utf-8")
    files.append(_entry_for(readme, relative=README_NAME))

    manifest = Manifest(
        schema=MANIFEST_SCHEMA,
        name=case.case_id,
        version=version,
        case_id=case.case_id,
        files=tuple(sorted(files, key=lambda entry: entry.path)),
        covered=selftest.covered,
        uncovered=selftest.uncovered,
        model=case.preferred_model,
        stamped=stamp,
    )
    _check_limits(manifest.files)
    (out_dir / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")
    return BuildResult(bundle_dir=out_dir, manifest=manifest, selftest=selftest)


def verify_bundle(bundle_dir: Path) -> VerifyResult:
    """校验 bundle 的完整性与判据有效性（**不执行模型**）。

    Raises:
        BundleError: bundle 目录/清单不可读、清单 schema 不符、路径越界或含符号链接。
            这些是"包本身不可判定"的情形，与"校验不通过"（返回值里的 ``problems``）分开。
    """
    base = Path(bundle_dir)
    if not base.is_dir():
        raise BundleError(f"bundle 目录不存在：{base}")
    manifest = _read_manifest(base)

    problems: list[str] = []
    expected = {entry.path: entry for entry in manifest.files}
    actual: dict[str, Path] = {}
    case_root = base / CASE_DIR_NAME
    if case_root.is_dir():
        for path in _walk_files(case_root):
            actual[path.relative_to(base).as_posix()] = path
    readme = base / README_NAME
    if readme.is_file():
        actual[README_NAME] = readme

    for relative in sorted(set(expected) - set(actual)):
        problems.append(f"缺少文件：{relative}")
    for relative in sorted(set(actual) - set(expected)):
        if relative == MANIFEST_NAME or relative.startswith(f"{EVIDENCE_DIR_NAME}/"):
            continue
        problems.append(f"清单外的多余文件：{relative}")
    for relative in sorted(set(expected) & set(actual)):
        entry = expected[relative]
        path = actual[relative]
        if path.stat().st_size != entry.size:
            problems.append(f"大小不一致：{relative}")
        elif _sha256(path) != entry.sha256:
            problems.append(f"摘要不一致：{relative}")

    selftest: SelftestResult | None = None
    if not problems:
        try:
            case = load_case_spec(case_root)
            selftest = run_selftest(case)
        except CaseSpecError as exc:
            problems.append(f"案例不可加载：{exc}")
        else:
            if not selftest.ok:
                problems.append("判据自证未通过（正例未全过或反例未失败）")

    return VerifyResult(
        bundle_dir=base, manifest=manifest, problems=tuple(problems), selftest=selftest
    )


# ---------------------------------------------------------------------------
# 内部
# ---------------------------------------------------------------------------


def _prepare_output(out_dir: Path, *, force: bool) -> None:
    if out_dir.exists():
        if not out_dir.is_dir():
            raise BundleError(f"输出路径不是目录：{out_dir}")
        if any(out_dir.iterdir()):
            if not force:
                raise BundleError(f"输出目录非空：{out_dir}（确需覆盖请显式 --force）")
            shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / EVIDENCE_DIR_NAME).mkdir(exist_ok=True)


def _walk_files(root: Path) -> list[Path]:
    """递归列出普通文件；**拒绝符号链接与 Python 内容**（``R5`` 同口径）。"""
    found: list[Path] = []
    for current, dirnames, filenames in os.walk(root, followlinks=False):
        base = Path(current)
        for name in dirnames:
            path = base / name
            if path.is_symlink():
                raise BundleError(f"案例目录含符号链接：{path.name}（拒绝打包）")
            if name == _FORBIDDEN_DIRECTORY:
                raise BundleError("案例目录含 __pycache__：只允许声明式资产")
        for name in filenames:
            path = base / name
            if path.is_symlink():
                raise BundleError(f"案例目录含符号链接：{path.name}（拒绝打包）")
            if name.endswith(_FORBIDDEN_SUFFIXES):
                raise BundleError("案例目录含 Python 源文件：只允许声明式资产")
            found.append(path)
    return sorted(found, key=lambda item: item.relative_to(root).as_posix())


def _entry_for(path: Path, *, relative: str) -> FileEntry:
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        raise BundleError(f"单文件超过上限（{MAX_FILE_BYTES} 字节）：{relative}")
    return FileEntry(path=relative, sha256=_sha256(path), size=size)


def _check_limits(files: tuple[FileEntry, ...]) -> None:
    if len(files) > MAX_FILES:
        raise BundleError(f"文件数超过上限（{MAX_FILES}）：{len(files)}")
    total = sum(entry.size for entry in files)
    if total > MAX_TOTAL_BYTES:
        raise BundleError(f"总体积超过上限（{MAX_TOTAL_BYTES} 字节）：{total}")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _read_manifest(base: Path) -> Manifest:
    path = base / MANIFEST_NAME
    if not path.is_file():
        raise BundleError(f"bundle 缺少 {MANIFEST_NAME}")
    if path.stat().st_size > MAX_MANIFEST_BYTES:
        raise BundleError("manifest.json 超过体积上限")
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise BundleError(f"manifest.json 不可读（{type(exc).__name__}）") from exc
    if not isinstance(payload, dict):
        raise BundleError("manifest.json 顶层必须是对象")
    return _parse_manifest(cast("Mapping[str, object]", payload))


def _parse_manifest(payload: Mapping[str, object]) -> Manifest:
    schema = _required_str(payload, "schema")
    if schema != MANIFEST_SCHEMA:
        raise BundleError(f"未知的 manifest schema：{schema}")
    criteria = payload.get("criteria")
    if not isinstance(criteria, dict):
        raise BundleError("manifest.criteria 必须是对象")
    raw_files = payload.get("files")
    if not isinstance(raw_files, list):
        raise BundleError("manifest.files 必须是数组")

    entries: list[FileEntry] = []
    seen: set[str] = set()
    for item in raw_files:
        if not isinstance(item, dict):
            raise BundleError("manifest.files 的每一项必须是对象")
        table = cast("Mapping[str, object]", item)
        relative = _required_str(table, "path")
        _validate_relative(relative)
        if relative in seen:
            raise BundleError(f"manifest.files 含重复路径：{relative}")
        seen.add(relative)
        digest = _required_str(table, "sha256")
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            raise BundleError(f"manifest.files 的 sha256 形状非法：{relative}")
        size = table.get("size")
        if isinstance(size, bool) or not isinstance(size, int) or size < 0:
            raise BundleError(f"manifest.files 的 size 非法：{relative}")
        entries.append(FileEntry(path=relative, sha256=digest, size=size))

    covered = _str_list(criteria, "covered")
    uncovered = _str_list(criteria, "uncovered")
    stamped = payload.get("stamped")
    if stamped is not None and not isinstance(stamped, str):
        raise BundleError("manifest.stamped 必须是字符串或省略")
    return Manifest(
        schema=schema,
        name=_required_str(payload, "name"),
        version=_required_str(payload, "version"),
        case_id=_required_str(payload, "case_id"),
        files=tuple(sorted(entries, key=lambda entry: entry.path)),
        covered=covered,
        uncovered=uncovered,
        model=_required_str(payload, "model"),
        stamped=stamped,
    )


def _validate_relative(relative: str) -> None:
    """清单路径必须是相对路径且不含 ``..``（防目录穿越）。"""
    pure = Path(relative)
    if pure.is_absolute() or relative.startswith("/") or ".." in pure.parts or not pure.parts:
        raise BundleError(f"manifest 含非法路径：{relative!r}")


def _required_str(payload: Mapping[str, object], key: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise BundleError(f"manifest 缺少字符串字段：{key}")
    return value


def _str_list(table: Mapping[str, object], key: str) -> tuple[str, ...]:
    value = table.get(key)
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise BundleError(f"manifest.criteria.{key} 必须是字符串数组")
    return tuple(cast("list[str]", value))


def _render_readme(*, case: CaseSpec, selftest: SelftestResult) -> str:
    """生成能力边界声明（人读）。**不写"已通过安全认证"一类结论。**"""
    lines = [
        "# 能力包（Capability Pack）",
        "",
        f"- 案例：`{case.case_id}` — {case.title}",
        f"- 模型（首选）：`{case.preferred_model}`",
        "",
        "## 这个包能做什么",
        "",
        "在内核给定的动作面内，完成该案例声明的两阶段任务；产物与审计可按其中记录的",
        "`audit_id` 回放。",
        "",
        "## 这个包不能证明什么",
        "",
        "- **不是安全认证**：隔离等级与残余风险以运行时报告为准；",
        "- **不是通用能力**：只对该案例的现场与判据成立，换任务必须重新编译；",
        "- **判据只覆盖部分维度**：见下方「判据覆盖」。",
        "",
        "## 判据覆盖",
        "",
        f"- 已自证：{'、'.join(selftest.covered) or '（无）'}",
        f"- 未自证：{'、'.join(selftest.uncovered) or '（无）'}",
        "",
        "## 判据自证",
        "",
        f"- 正例全过：{'是' if selftest.positive_ok else '否'}",
        f"- 反例至少失败一项：{'是' if selftest.negative_ok else '否'}",
        "",
        "> 本文由 `agent-sec-perf pack build` 生成；不要手工编辑（会被摘要校验发现）。",
        "",
    ]
    return "\n".join(lines)
