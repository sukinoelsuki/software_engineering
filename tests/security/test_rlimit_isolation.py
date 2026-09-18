"""T-01②（威胁模型 T-01 资源耗尽）：隔离执行的子进程必须受 RLIMIT_AS 约束。

背景（已核实）：``foundation/proc.py`` 的 ``_apply_limits``（:113-118）在
``run(isolation="user")`` 经 ``preexec_fn`` 施加 ``RLIMIT_AS``（:39，2 GiB）。
子进程若尝试超分配，应**确定性地触发 MemoryError / OSError（或被内核杀掉）**，
而非静默成功——否则攻击者可借超分配实施资源耗尽（DoS）。

本用例的断言是「确定性」的：让子进程尝试映射 3 GiB（> 2 GiB 上限，且只用虚拟地址、
不触碰物理内存，不会拖垮机器），断言其**未能成功**完成映射
（输出中不得出现 ``ALLOC_OK``）。无论子进程是抛异常还是被 OOM 杀掉，结论一致。

变异验证（开发期手动）：临时把 ``_apply_limits`` 改为 no-op（monkeypatch），
子进程即可成功映射 3 GiB 并打印 ``ALLOC_OK``，本用例随之失败，证明它依赖真实保护。
"""

from __future__ import annotations

import pathlib
import sys

import pytest

from agent_sec_perf.foundation import proc

# 子进程尝试映射的字节数：明确大于 RLIMIT_AS（2 GiB），但仅占用虚拟地址、不触碰物理内存。
_OVER_ALLOC_BYTES = 3 * 1024 * 1024 * 1024

_CHILD_CODE = (
    "import mmap, sys\n"
    "try:\n"
    "    mmap.mmap(-1, " + str(_OVER_ALLOC_BYTES) + ")\n"
    "    sys.stdout.write('ALLOC_OK')\n"
    "except (MemoryError, OSError):\n"
    "    sys.stdout.write('MEMORY_LIMIT_HIT')\n"
)


@pytest.mark.security
def test_isolated_child_cannot_overallocate_address_space(
    tmp_path: pathlib.Path,
) -> None:
    """隔离子进程超分配 (>RLIMIT_AS) 必须失败，而非静默成功。"""
    result = proc.run(
        [sys.executable, "-c", _CHILD_CODE],
        cwd=tmp_path,
        timeout_s=30.0,
        isolation="user",
    )
    # 关键断言：子进程不得成功分配超过上限的地址空间。
    # 受 RLIMIT_AS 约束时，映射会抛异常（输出 MEMORY_LIMIT_HIT）或被杀（无输出）。
    assert "ALLOC_OK" not in result.output(), (
        "隔离子进程成功映射了超过 RLIMIT_AS 的内存（3 GiB），"
        "资源上限未生效，存在资源耗尽/拒绝服务风险。"
    )


@pytest.mark.security
def test_rlimit_guard_depends_on_apply_limits(
    tmp_path: pathlib.Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """变异验证：移除 RLIMIT_AS 后，子进程能成功超分配 ⇒ 上方用例必须失败。

    把 ``_apply_limits`` 替换为 no-op，模拟「保护被移除」；此时子进程应顺利映射 3 GiB
    并打印 ``ALLOC_OK``——证明上方对抗性用例是通过真实保护才通过的，而非偶然。
    """
    monkeypatch.setattr(proc, "_apply_limits", lambda: None)
    result = proc.run(
        [sys.executable, "-c", _CHILD_CODE],
        cwd=tmp_path,
        timeout_s=30.0,
        isolation="user",
    )
    # 保护被移除 ⇒ 攻击得手：映射成功。
    assert "ALLOC_OK" in result.output(), (
        "变异验证失败：移除 _apply_limits 后子进程仍未成功映射，"
        "说明 _apply_limits 并非该保护的有效来源。"
    )
