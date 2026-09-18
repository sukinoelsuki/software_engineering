"""路径解析与白名单校验。

所有来自命令行或环境变量的路径都必须经过 :func:`resolve_within`：
先规范化（``resolve()`` 会展开 ``..`` 与符号链接），再要求落在允许的根目录内。
禁止用字符串拼接构造路径，也禁止"看起来像"就直接使用。
"""

from __future__ import annotations

import pathlib
from collections.abc import Sequence

from agent_sec_perf.foundation.errors import PathNotAllowedError


def resolve_within(
    candidate: str | pathlib.Path,
    roots: Sequence[pathlib.Path],
    *,
    what: str,
) -> pathlib.Path:
    """把候选路径规范化为绝对路径，并要求它位于某个允许的根目录内。

    Args:
        candidate: 待校验的路径（可能来自命令行）。
        roots: 允许的根目录集合。
        what: 出错信息里对该路径的称呼，便于定位是哪个参数有问题。

    Raises:
        PathNotAllowedError: 路径不在任何允许的根目录内。
    """
    resolved = pathlib.Path(candidate).expanduser().resolve()
    for root in roots:
        root_resolved = root.expanduser().resolve()
        if resolved == root_resolved or root_resolved in resolved.parents:
            return resolved
    allowed = "、".join(str(root) for root in roots)
    msg = f"{what} 不在允许的根目录内：{resolved}（允许：{allowed}）"
    raise PathNotAllowedError(msg)


def make_writable_by_all(path: pathlib.Path) -> None:
    """把目录设为任何用户可进入/可写。

    用途：隔离执行时子进程以非特权 uid 运行，需要能进入工作目录并写产物。
    仅用于**一次性工作目录**，不得对仓库目录调用。
    """
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o777)
