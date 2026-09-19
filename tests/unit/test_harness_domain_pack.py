"""``domain_pack`` 的行为断言（契约 §4；验收 ``H-5``）。

这是实现侧的**功能**断言。对抗性断言（"包内 ``.py`` 不得被执行、不得进入 ``sys.modules``"，
即 ``ADR-0015`` §7.2 的 ``S3``）属 ``tests/security/``，由验证角色独立完成
（安全断言不得由实现者自证）；这里只落 ``H-5`` 的五类失败与合法字段一致性。

变异探针（说明这些断言不是"陪跑"，逐条能被一个具体改动杀死）：

* 删掉顶层未知段的检查 ⇒ :func:`test_unknown_section_is_rejected` 失败；
* 把 ``_reject_python_content`` 里的 ``endswith((".py", ".pyc"))`` 去掉 ⇒
  :func:`test_python_source_file_is_rejected` 失败；
* 把 ``roots`` 从 keyword-only 改成有默认值 ⇒
  :func:`test_roots_and_known_tools_are_required_keyword_only` 失败；
* 把未知工具名由"拒绝"改成"跳过" ⇒ :func:`test_unknown_tool_name_is_rejected` 失败；
* 把未知能力名由"拒绝"改成"丢弃" ⇒ :func:`test_unknown_capability_name_is_rejected` 失败；
* 删掉 ``_optional_str_list`` 里对 ``fragments`` 的形状检查 ⇒
  :func:`test_wrong_types_are_rejected` 失败。
"""

from __future__ import annotations

from pathlib import Path

import pytest

from agent_sec_perf.contracts.policy import Capability, RiskLevel
from agent_sec_perf.foundation.errors import BenchError, PathNotAllowedError
from agent_sec_perf.harness.domain_pack import DomainPack, load_pack
from agent_sec_perf.harness.errors import DomainPackError

#: 与内置工具同形的已知工具名（``known_tools`` 的测试取值）。
KNOWN_TOOLS = frozenset({"read_file", "list_dir", "write_file", "run_command"})

#: 唯一 sentinel：写进 TOML，但**不得**出现在异常消息中（不回显原文片段）。
_SENTINEL = "SENTINEL-7f3a-not-for-logs"

VALID_PACK = """
[pack]
name = "code-review"
version = "1.0.0"
description = "代码评审场景"

[prompt]
fragments = ["评审时先列出改动点", "再给出结论"]

[tools]
allowlist = ["read_file", "list_dir"]

[security]
capabilities = ["read_file"]

[security.risk_overrides]
write_file = "high"

[output]
format = "markdown"
"""

MINIMAL_PACK = """
[pack]
name = "minimal"
version = "0.1.0"

[tools]
allowlist = []

[security]
capabilities = []
"""


def _write_pack(directory: Path, content: str) -> Path:
    """在 ``directory`` 下写一份 ``pack.toml``（不调用生产代码构造）。"""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "pack.toml").write_text(content, encoding="utf-8")
    return directory


def _load(directory: Path, *, roots: tuple[Path, ...], known_tools: frozenset[str] = KNOWN_TOOLS):
    return load_pack(directory, roots=roots, known_tools=known_tools)


# ---------------------------------------------------------------------------
# 正向：合法 pack 的字段逐项一致
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_valid_pack_fields_match_the_toml(tmp_path: Path) -> None:
    """合法 pack 的每个字段都与 ``pack.toml`` 一致（``H-5`` 的后半句）。"""
    directory = _write_pack(tmp_path / "pack", VALID_PACK)

    pack = _load(directory, roots=(tmp_path,))

    assert isinstance(pack, DomainPack)
    assert pack.name == "code-review"
    assert pack.version == "1.0.0"
    assert pack.prompt_fragments == ("评审时先列出改动点", "再给出结论")
    assert pack.tool_allowlist == frozenset({"read_file", "list_dir"})
    assert pack.capabilities_allowlist == frozenset({Capability.READ_FILE})
    assert dict(pack.risk_overrides) == {"write_file": RiskLevel.HIGH}
    assert pack.output_format == "markdown"


@pytest.mark.unit
def test_minimal_pack_needs_only_the_required_sections(tmp_path: Path) -> None:
    """只给 ``[pack]`` / ``[tools]`` / ``[security]`` 也能加载：其余段可选，缺省为"空"。"""
    directory = _write_pack(tmp_path / "pack", MINIMAL_PACK)

    pack = _load(directory, roots=(tmp_path,))

    assert pack.prompt_fragments == ()
    assert pack.tool_allowlist == frozenset()
    assert pack.capabilities_allowlist == frozenset()
    assert dict(pack.risk_overrides) == {}
    assert pack.output_format is None


@pytest.mark.unit
def test_empty_allowlists_mean_deny_all_not_unrestricted(tmp_path: Path) -> None:
    """空数组是"什么都不给"（**可表达**的取值），不是"不限制"（fail-secure）。"""
    directory = _write_pack(tmp_path / "pack", MINIMAL_PACK)

    pack = _load(directory, roots=(tmp_path,))

    assert pack.tool_allowlist == frozenset()
    assert pack.capabilities_allowlist == frozenset()


@pytest.mark.unit
def test_source_points_to_the_resolved_pack_toml(tmp_path: Path) -> None:
    """``source`` 是规范化后的 ``pack.toml`` 路径（证据用）。"""
    directory = _write_pack(tmp_path / "pack", MINIMAL_PACK)

    pack = _load(directory, roots=(tmp_path,))

    assert pack.source == (tmp_path / "pack" / "pack.toml").resolve()
    assert pack.source.is_file()


@pytest.mark.unit
def test_non_python_files_are_tolerated(tmp_path: Path) -> None:
    """目录里的说明文档 / 数据文件可以存在（``P1``：不被读取，也不报错）。"""
    directory = _write_pack(tmp_path / "pack", MINIMAL_PACK)
    (directory / "README.md").write_text("说明", encoding="utf-8")
    (directory / "example.json").write_text("{}", encoding="utf-8")

    pack = _load(directory, roots=(tmp_path,))

    assert pack.name == "minimal"


@pytest.mark.unit
def test_python_file_outside_the_pack_directory_is_irrelevant(tmp_path: Path) -> None:
    """包目录**之外**的 ``.py`` 与本次加载无关（只判定目标目录本身）。"""
    sibling = tmp_path / "sibling"
    sibling.mkdir()
    (sibling / "tool.py").write_text("print('x')\n", encoding="utf-8")
    directory = _write_pack(tmp_path / "pack", MINIMAL_PACK)

    pack = _load(directory, roots=(tmp_path,))

    assert pack.name == "minimal"


# ---------------------------------------------------------------------------
# 拒绝：未知段 / 未知键 / 未知名字（§4.3）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_unknown_section_is_rejected(tmp_path: Path) -> None:
    """未知**段** ⇒ ``DomainPackError``（不得忽略）。"""
    directory = _write_pack(tmp_path / "pack", MINIMAL_PACK + "\n[extra]\nkey = 1\n")

    with pytest.raises(DomainPackError, match="未定义的段"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [
        # 每个已知段各塞一个未知键（含可选段）。
        MINIMAL_PACK.replace('name = "minimal"', 'name = "minimal"\nnickname = "x"'),
        MINIMAL_PACK.replace("allowlist = []", "allowlist = []\ndenylist = []"),
        MINIMAL_PACK.replace("capabilities = []", 'capabilities = []\nmode = "strict"'),
        MINIMAL_PACK + '\n[prompt]\nfragments = []\nupper = "x"\n',
        MINIMAL_PACK + '\n[output]\nformat = "text"\nstyle = "fancy"\n',
    ],
)
def test_unknown_key_in_known_section_is_rejected(tmp_path: Path, content: str) -> None:
    """已知段里的未知键 ⇒ ``DomainPackError``（未知键是"以为生效、实际没生效"的来源）。"""
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="未定义的键"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_unknown_tool_name_is_rejected(tmp_path: Path) -> None:
    """``tools.allowlist`` 里的未知工具名 ⇒ ``DomainPackError``（不跳过）。"""
    directory = _write_pack(
        tmp_path / "pack",
        MINIMAL_PACK.replace("allowlist = []", 'allowlist = ["ghost_tool"]'),
    )

    with pytest.raises(DomainPackError, match="未知工具名"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_unknown_capability_name_is_rejected(tmp_path: Path) -> None:
    """``security.capabilities`` 里的未知能力名 ⇒ ``DomainPackError``（不跳过）。"""
    directory = _write_pack(
        tmp_path / "pack",
        MINIMAL_PACK.replace("capabilities = []", 'capabilities = ["telepathy"]'),
    )

    with pytest.raises(DomainPackError, match="未知能力名"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_unknown_risk_level_is_rejected(tmp_path: Path) -> None:
    """``security.risk_overrides`` 的未知风险等级 ⇒ ``DomainPackError``（不跳过）。"""
    content = MINIMAL_PACK + '\n[security.risk_overrides]\nread_file = "apocalyptic"\n'
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="未知风险等级"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_risk_override_for_unknown_tool_is_rejected(tmp_path: Path) -> None:
    """``security.risk_overrides`` 的键必须是已知工具名。"""
    content = MINIMAL_PACK + '\n[security.risk_overrides]\nghost_tool = "high"\n'
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="未知工具名"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_output_format_outside_the_enum_is_rejected(tmp_path: Path) -> None:
    """``output.format`` 只接受本轮固定小集合（``markdown`` / ``text``）。"""
    content = MINIMAL_PACK + '\n[output]\nformat = "html"\n'
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match=r"output\.format"):
        _load(directory, roots=(tmp_path,))


# ---------------------------------------------------------------------------
# 拒绝：缺必填 / 形状 / 类型 / 超长（§4.3）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_missing_pack_section_is_rejected(tmp_path: Path) -> None:
    """缺 ``[pack]`` 段 ⇒ ``DomainPackError``。"""
    directory = _write_pack(
        tmp_path / "pack",
        "[tools]\nallowlist = []\n\n[security]\ncapabilities = []\n",
    )

    with pytest.raises(DomainPackError, match=r"\[pack\]"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
@pytest.mark.parametrize("missing", ["name", "version"])
def test_missing_required_pack_key_is_rejected(tmp_path: Path, missing: str) -> None:
    """缺 ``pack.name`` / ``pack.version`` ⇒ ``DomainPackError``。"""
    if missing == "name":
        content = MINIMAL_PACK.replace('name = "minimal"\n', "")
    else:
        content = MINIMAL_PACK.replace('version = "0.1.0"\n', "")
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="缺少必填键"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_missing_tools_allowlist_is_rejected(tmp_path: Path) -> None:
    """缺 ``tools.allowlist`` ⇒ ``DomainPackError``（漏写不得等价于"不限制"）。"""
    content = MINIMAL_PACK.replace("allowlist = []\n", "")
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="allowlist"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_missing_security_capabilities_is_rejected(tmp_path: Path) -> None:
    """缺 ``security.capabilities`` 键 ⇒ ``DomainPackError``（``H-5`` 明列的一类）。"""
    content = MINIMAL_PACK.replace("capabilities = []\n", "")
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="capabilities"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
@pytest.mark.parametrize("bad_name", ["Code-Review", "-leading", "has space", "", "a" * 65])
def test_invalid_pack_name_shape_is_rejected(tmp_path: Path, bad_name: str) -> None:
    """``pack.name`` 形状不符（``^[a-z0-9][a-z0-9-]{0,63}$``）⇒ ``DomainPackError``。"""
    content = MINIMAL_PACK.replace('name = "minimal"', f'name = "{bad_name}"')
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match=r"pack\.name"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
@pytest.mark.parametrize("bad_version", ["1.0", "v1.0.0", "1.0.0-rc1", "x.y.z"])
def test_invalid_version_shape_is_rejected(tmp_path: Path, bad_version: str) -> None:
    """``pack.version`` 形状不符（``MAJOR.MINOR.PATCH``）⇒ ``DomainPackError``。"""
    content = MINIMAL_PACK.replace('version = "0.1.0"', f'version = "{bad_version}"')
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match=r"pack\.version"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_too_many_fragments_is_rejected(tmp_path: Path) -> None:
    """``prompt.fragments`` 最多 8 条。"""
    fragments = ", ".join(f'"f{index}"' for index in range(9))
    content = MINIMAL_PACK + f"\n[prompt]\nfragments = [{fragments}]\n"
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="8 条"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_too_long_fragment_is_rejected(tmp_path: Path) -> None:
    """单条片段 ≤ 2000 字符。"""
    long_fragment = "x" * 2001
    content = MINIMAL_PACK + f'\n[prompt]\nfragments = ["{long_fragment}"]\n'
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="2000"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_too_long_description_is_rejected(tmp_path: Path) -> None:
    """``pack.description`` ≤ 500 字符。"""
    long_description = "x" * 501
    content = MINIMAL_PACK.replace(
        'name = "minimal"', f'name = "minimal"\ndescription = "{long_description}"'
    )
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError, match="description"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
@pytest.mark.parametrize(
    "content",
    [
        # name 不是字符串
        MINIMAL_PACK.replace('name = "minimal"', "name = 7"),
        # allowlist 不是数组
        MINIMAL_PACK.replace("allowlist = []", 'allowlist = "read_file"'),
        # allowlist 元素不是字符串
        MINIMAL_PACK.replace("allowlist = []", "allowlist = [7]"),
        # capabilities 不是数组
        MINIMAL_PACK.replace("capabilities = []", "capabilities = 1"),
        # risk_overrides 不是表
        MINIMAL_PACK + "\n[security.risk_overrides]\nread_file = 5\n",
        # prompt 段不是表
        MINIMAL_PACK + "\nprompt = 1\n",
    ],
)
def test_wrong_types_are_rejected(tmp_path: Path, content: str) -> None:
    """类型不符（字符串 / 数组 / 表）⇒ ``DomainPackError``。"""
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError):
        _load(directory, roots=(tmp_path,))


# ---------------------------------------------------------------------------
# 拒绝：包内 Python 内容（``P2`` / ``R5``）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_python_source_file_is_rejected(tmp_path: Path) -> None:
    """目录内有 ``.py`` ⇒ ``DomainPackError``（不得"忽略它继续加载"）。"""
    directory = _write_pack(tmp_path / "pack", VALID_PACK)
    (directory / "evil.py").write_text("raise SystemExit\n", encoding="utf-8")

    with pytest.raises(DomainPackError, match="Python"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_bytecode_file_is_rejected(tmp_path: Path) -> None:
    """目录内有 ``.pyc`` ⇒ ``DomainPackError``。"""
    directory = _write_pack(tmp_path / "pack", VALID_PACK)
    (directory / "evil.pyc").write_bytes(b"\x00\x01")

    with pytest.raises(DomainPackError, match="Python"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_pycache_directory_is_rejected(tmp_path: Path) -> None:
    """目录内有 ``__pycache__`` ⇒ ``DomainPackError``（它意味着曾有代码被执行）。"""
    directory = _write_pack(tmp_path / "pack", VALID_PACK)
    (directory / "__pycache__").mkdir()

    with pytest.raises(DomainPackError, match="__pycache__"):
        _load(directory, roots=(tmp_path,))


# ---------------------------------------------------------------------------
# 路径校验（``P3`` / ``P4``）与 I/O 故障分级
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_path_outside_roots_is_rejected(tmp_path: Path) -> None:
    """``directory`` 不在 ``roots`` 内 ⇒ ``PathNotAllowedError``（先校验后读）。"""
    directory = _write_pack(tmp_path / "outside" / "pack", VALID_PACK)
    allowed_root = tmp_path / "allowed"
    allowed_root.mkdir()

    with pytest.raises(PathNotAllowedError):
        _load(directory, roots=(allowed_root,))


@pytest.mark.unit
def test_symlink_escape_is_rejected(tmp_path: Path) -> None:
    """符号链接指向白名单外 ⇒ ``PathNotAllowedError``（``resolve()`` 展开后判定）。"""
    outside = _write_pack(tmp_path / "outside", VALID_PACK)
    inside = tmp_path / "allowed" / "inside"
    inside.mkdir(parents=True)
    link = inside / "link"
    link.symlink_to(outside, target_is_directory=True)

    with pytest.raises(PathNotAllowedError):
        _load(link, roots=(tmp_path / "allowed",))


@pytest.mark.unit
def test_roots_and_known_tools_are_required_keyword_only(tmp_path: Path) -> None:
    """``roots`` 与 ``known_tools`` 必填且 keyword-only（无默认值 ⇒ 不会退化为"任意路径"）。"""
    directory = _write_pack(tmp_path / "pack", MINIMAL_PACK)

    with pytest.raises(TypeError):
        load_pack(directory)  # type: ignore[call-arg]

    with pytest.raises(TypeError):
        load_pack(directory, (tmp_path,))  # type: ignore[call-arg,misc]

    with pytest.raises(TypeError):
        load_pack(directory, roots=(tmp_path,))  # type: ignore[call-arg]


@pytest.mark.unit
def test_missing_pack_toml_is_rejected(tmp_path: Path) -> None:
    """缺 ``pack.toml`` ⇒ ``DomainPackError``。"""
    directory = tmp_path / "pack"
    directory.mkdir()

    with pytest.raises(DomainPackError, match=r"pack\.toml"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_pack_toml_must_be_a_regular_file(tmp_path: Path) -> None:
    """``pack.toml`` 是目录（非普通文件）⇒ ``DomainPackError``。"""
    directory = tmp_path / "pack"
    (directory / "pack.toml").mkdir(parents=True)

    with pytest.raises(DomainPackError, match=r"pack\.toml"):
        _load(directory, roots=(tmp_path,))


@pytest.mark.unit
def test_directory_that_is_not_a_directory_raises_oserror(tmp_path: Path) -> None:
    """``directory`` 是普通文件 ⇒ ``OSError``（环境故障原样冒泡，不包装成 ``DomainPackError``）。"""
    not_a_directory = tmp_path / "file.txt"
    not_a_directory.write_text("x", encoding="utf-8")

    with pytest.raises(OSError):
        _load(not_a_directory, roots=(tmp_path,))


@pytest.mark.unit
def test_toml_syntax_error_is_rejected_without_echoing_content(tmp_path: Path) -> None:
    """TOML 语法错误 ⇒ ``DomainPackError``，且消息**不含**原文片段（§4.3）。"""
    content = f'[pack]\nname = "{_SENTINEL}"\nversion =\n'
    directory = _write_pack(tmp_path / "pack", content)

    with pytest.raises(DomainPackError) as excinfo:
        _load(directory, roots=(tmp_path,))

    assert _SENTINEL not in str(excinfo.value)


@pytest.mark.unit
def test_error_type_is_a_project_error(tmp_path: Path) -> None:
    """失败类型是项目基类 ``BenchError`` 的子类（``cli/`` 的退出码分类不会漏接）。"""
    directory = tmp_path / "pack"
    directory.mkdir()

    with pytest.raises(DomainPackError) as excinfo:
        _load(directory, roots=(tmp_path,))

    assert isinstance(excinfo.value, BenchError)


# ---------------------------------------------------------------------------
# 不可变性：``risk_overrides`` 只读
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_risk_overrides_mapping_is_read_only(tmp_path: Path) -> None:
    """``risk_overrides`` 是只读视图：调用方不得就地改动已加载的风险覆盖。"""
    directory = _write_pack(tmp_path / "pack", VALID_PACK)

    pack = _load(directory, roots=(tmp_path,))

    with pytest.raises(TypeError):
        pack.risk_overrides["write_file"] = RiskLevel.LOW  # type: ignore[index]
