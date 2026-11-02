"""内置示例案例：定位与复制。

为什么需要这一层：``pack build`` 接受**任意目录**里的案例，但安装成 wheel 之后
没有仓库根，``examples/showcase/incident-01`` 也不在源码树里。示例必须能随包分发，
否则"装好就能跑一次"只在源码仓库里成立。

两条来源，按顺序取：

1. **wheel 内的包数据**：``agent_sec_perf/data/examples/showcase/incident-01``
   （由 ``pyproject.toml`` 的 ``force-include`` 从仓库 ``examples/`` 复制）；
2. **源码仓库**：``<repo>/examples/showcase/incident-01``（开发与测试时走这条）。

两条都不存在时抛 :class:`BenchError`。**不猜路径、不下载**：示例缺失是装配期故障，
应当明确报出来，而不是让调用方在别处失败。
"""

from __future__ import annotations

import shutil
from pathlib import Path

from agent_sec_perf.foundation.errors import BenchError

__all__ = ["EXAMPLE_CASE_ID", "copy_example", "example_case_dir"]

#: 示例案例的目录名（也是 ``case.toml`` 的 ``id``）。
EXAMPLE_CASE_ID = "incident-01"

#: wheel 内的相对路径（相对 ``agent_sec_perf`` 包根）。
_PACKAGED_RELATIVE = Path("data") / "examples" / "showcase" / EXAMPLE_CASE_ID


def example_case_dir() -> Path:
    """返回内置示例案例的目录（只读；需要可写副本请用 :func:`copy_example`）。

    Raises:
        BenchError: wheel 包数据与源码仓库都找不到示例。
    """
    packaged = _packaged_example()
    if packaged is not None:
        return packaged

    source = Path(__file__).resolve().parents[3] / "examples" / "showcase" / EXAMPLE_CASE_ID
    if (source / "case.toml").is_file():
        return source

    raise BenchError(
        "未找到内置示例案例：wheel 缺少 data/examples，源码树也缺少 "
        f"examples/showcase/{EXAMPLE_CASE_ID}"
    )


def copy_example(out_dir: Path, *, force: bool = False) -> Path:
    """把内置示例复制到 ``out_dir``，返回目标目录。

    ``force=False`` 时目标必须不存在或为空。``force=True`` 只覆盖同名文件，
    **不删除**目标里已有的其他文件——示例是声明式资产，误删用户目录的代价
    高于留下一个多余文件。

    Raises:
        BenchError: 目标与示例源重叠、目标是文件、或目录非空且未给 ``force``。
        OSError: 复制失败（磁盘满、权限不足等）。
    """
    source = example_case_dir().resolve()
    target = Path(out_dir).resolve()

    # 目标不能是源本身、源的子目录、或源的父目录：三种情况都可能覆盖/删除示例。
    if target == source or target.is_relative_to(source) or source.is_relative_to(target):
        raise BenchError(f"输出目录不能与示例源重叠：{target}")

    if target.exists():
        if not target.is_dir():
            raise BenchError(f"输出路径不是目录：{target}")
        if any(target.iterdir()) and not force:
            raise BenchError(f"输出目录非空：{target}（确需覆盖请显式 --force）")

    target.parent.mkdir(parents=True, exist_ok=True)
    # 目标可能是空目录（上面的检查已允许）：``dirs_exist_ok`` 必须为 True，
    # 否则 copytree 会因为目标已存在而报错。非空且未给 force 的情况已在上方拒绝。
    shutil.copytree(source, target, dirs_exist_ok=True)
    return target


def _packaged_example() -> Path | None:
    """读 wheel 内的示例目录；源码树 / 未打包时返回 ``None``。

    按包目录定位：``agent_sec_perf/pack/example.py`` 的上一级是包根，wheel 内
    ``data/examples`` 就在包根下。本项目只发布 wheel（pip / uv 解包安装），
    因此按普通文件系统目录处理；若将来支持 zip 导入，需要额外的资源访问层。
    """
    candidate = Path(__file__).resolve().parents[1] / _PACKAGED_RELATIVE
    try:
        if (candidate / "case.toml").is_file():
            return candidate
    except OSError:
        return None
    return None
