#!/usr/bin/env bash
# AI coding 笔试 · 自动判分脚本
#
# 用法：
#   bash exam/verify.sh [工作目录]        # 省略工作目录 ⇒ 当前目录
#
# 它做三件事（**只读**考生的源码，不改动它）：
#   1. 用题目规定的命令编译 student.c（-std=c11 -Wall -Wextra -O2）；
#   2. 把 exam/cases/caseN.in 逐个喂给程序，与 caseN.expected 逐行比对；
#   3. 打印报告并给出分数。
#
# 退出码：0 = 4 个用例全过；1 = 有用例未过或编译失败；2 = 用法/环境错误。
#
# 比对口径（**明说，不含糊**）：忽略每行**行尾空白**与文件**末尾的空行**；其余逐字符严格相等。
# 一致性比容错重要——输出是一份协议，宽容会掩盖"格式错"这类真实缺陷。

set -uo pipefail

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
CASES_DIR="$SCRIPT_DIR/cases"
CC=${CC:-gcc}
CFLAGS=${CFLAGS:--std=c11 -Wall -Wextra -O2}
RUN_TIMEOUT_S=${RUN_TIMEOUT_S:-30}

WORK_DIR=${1:-$PWD}
if [ ! -d "$WORK_DIR" ]; then
    echo "用法错误：工作目录不存在：$WORK_DIR" >&2
    exit 2
fi
WORK_DIR=$(cd "$WORK_DIR" && pwd)

SOURCE="$WORK_DIR/student.c"
if [ ! -f "$SOURCE" ]; then
    echo "找不到源文件：$SOURCE" >&2
    echo "（题目要求交付物是工作目录下的 student.c）" >&2
    exit 2
fi
if ! command -v "$CC" >/dev/null 2>&1; then
    echo "环境错误：找不到 C 编译器 $CC" >&2
    exit 2
fi

BUILD_LOG="$WORK_DIR/.verify-build.log"
BIN="$WORK_DIR/student"

echo "============================================================"
echo " AI coding 笔试 · 判分报告"
echo " 工作目录：$WORK_DIR"
echo " 编译器  ：$($CC --version 2>/dev/null | head -1)"
echo " 编译命令：$CC $CFLAGS -o student student.c"
echo "============================================================"

if ! "$CC" $CFLAGS -o "$BIN" "$SOURCE" 2>"$BUILD_LOG"; then
    echo "[编译] ✗ 失败"
    echo "---- 编译器输出 ----"
    sed -e 's/^/  /' "$BUILD_LOG"
    echo "--------------------"
    echo "结论：编译未通过 ⇒ 0 分。"
    exit 1
fi
WARNING_COUNT=$(grep -c 'warning:' "$BUILD_LOG" 2>/dev/null || true)
WARNING_COUNT=${WARNING_COUNT:-0}
echo "[编译] ✓ 通过（警告 $WARNING_COUNT 条）"

normalize() {
    # 去掉行尾空白，再去掉末尾空行；其余原样。
    sed -e 's/[[:space:]]*$//' "$1" | awk '
        { lines[NR] = $0 }
        END { last = NR; while (last > 0 && lines[last] == "") last--
              for (i = 1; i <= last; i++) print lines[i] }'
}

PASSED=0
TOTAL=0
FAILED_CASES=""

for case_file in "$CASES_DIR"/case*.in; do
    [ -e "$case_file" ] || continue
    TOTAL=$((TOTAL + 1))
    name=$(basename "$case_file" .in)
    expected="$CASES_DIR/$name.expected"
    actual=$(mktemp)
    run_err=$(mktemp)

    # 在**考生的工作目录**里运行（题目允许程序按相对路径读写），stdout/stderr 分别落盘。
    ( cd "$WORK_DIR" && timeout "$RUN_TIMEOUT_S" "$BIN" < "$case_file" > "$actual" 2>"$run_err" )
    status=$?

    if [ "$status" -eq 124 ]; then
        echo "[$name] ✗ 超时（>${RUN_TIMEOUT_S}s）——程序可能没有处理到 EOF，或在等待更多输入"
        FAILED_CASES="$FAILED_CASES $name"
        rm -f "$actual" "$run_err"
        continue
    fi
    if [ "$status" -ne 0 ]; then
        echo "[$name] ✗ 退出码非 0（$status）——题目要求正常处理到 EOF 后以 0 退出"
        if [ -s "$run_err" ]; then sed -e 's/^/    /' "$run_err" | head -5; fi
        FAILED_CASES="$FAILED_CASES $name"
        rm -f "$actual" "$run_err"
        continue
    fi

    if diff -u <(normalize "$expected") <(normalize "$actual") > /tmp/.verify_diff.$$ 2>&1; then
        echo "[$name] ✓ 通过"
        PASSED=$((PASSED + 1))
    else
        echo "[$name] ✗ 输出不符（约定：左 = 期望，右 = 实际）"
        head -20 /tmp/.verify_diff.$$ | sed -e 's/^/    /'
        FAILED_CASES="$FAILED_CASES $name"
    fi
    rm -f "$actual" "$run_err"
done
rm -f /tmp/.verify_diff.$$
rm -f "$BUILD_LOG"

if [ "$TOTAL" -eq 0 ]; then
    echo "环境错误：$CASES_DIR 下没有用例文件" >&2
    exit 2
fi

SCORE=$((PASSED * 100 / TOTAL))
echo "------------------------------------------------------------"
echo " 用例通过：$PASSED / $TOTAL"$([ -n "$FAILED_CASES" ] && echo "（未过：$FAILED_CASES）")
echo " 得分    ：$SCORE / 100"
echo " 编译警告：$WARNING_COUNT 条（加分项；不参与上面的分数）"
echo " 编译产物：$BIN"
if [ "$WARNING_COUNT" -gt 0 ]; then
    echo " 提示    ：有警告时建议先看 $CC 的原始输出（本次已清理，重新编译即可复现）："
    echo "           $CC $CFLAGS -o /tmp/student-check $SOURCE"
fi
echo "------------------------------------------------------------"

if [ "$PASSED" -eq "$TOTAL" ]; then
    echo "结论：全部通过。"
    exit 0
fi
echo "结论：未全部通过 ⇒ 把上面的差异信息交给模型，让它改（这正是笔试要练的部分）。"
exit 1
