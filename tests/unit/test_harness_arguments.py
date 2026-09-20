"""``SubsetArgumentValidator`` 的行为断言（契约 ``H-3`` / ``H-11``；``ADR-0020`` §5 与 §7）。

这是实现侧的**功能**断言。校验器是信任边界本身（输入是来自模型的原始 JSON 文本 ⇒
一律不可信），因此失败路径的用例占比刻意高于正常路径。

覆盖两块：

* ``H-3``：超大 JSON / 未知键 / 类型不符 / 缺必填 / 非法 JSON ⇒ 全部
  ``ToolArgumentsInvalidError``，且错误消息**不含**参数值 sentinel（``V4`` / ``I8``）；
* ``H-11``：① 子集边界逐类正反例；② 子集外的关键字 ⇒ 拒绝（**不得**静默忽略）+
  元测试证明该守卫真会触发；③ 机器检查——全部内置工具的 ``parameters_schema``
  关键字集合 ⊆ ``ADR-0020`` §5.1 的白名单。

变异探针（逐条能被一个具体改动杀死）：

* 把 ``_require_present_keys`` 的 ``name not in payload`` 改成 ``name in payload`` ⇒
  :func:`test_missing_required_key_is_rejected` 失败；
* 去掉 ``_validate_integer`` 的 ``isinstance(value, bool)`` ⇒
  :func:`test_bool_is_not_an_integer` 失败；
* 把 ``_reject_extra_keywords`` 改成空操作 ⇒
  :func:`test_unsupported_property_keyword_is_rejected` 与其元测试失败；
* 把 ``_invalid`` 改成拼接 ``arguments_json`` ⇒
  :func:`test_error_message_does_not_echo_payload_fragments` 失败；
* 把 ``_reject_unknown_keys`` 改成回显未知键名 ⇒
  :func:`test_error_message_does_not_echo_unknown_key_name` 失败；
* 去掉 ``_check_size`` 的字节检查 ⇒
  :func:`test_oversized_bytes_are_rejected_without_parsing` 失败；
* 去掉 ``_parse_json_object`` 的 ``_DuplicateKeyError`` 护栏 ⇒
  :func:`test_duplicate_object_key_is_rejected` 失败；
* 去掉 ``_validate_number`` 的 ``math.isfinite`` ⇒
  :func:`test_non_finite_number_is_rejected` 失败。
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Final

import pytest

from agent_sec_perf.contracts.audit import AuditEvent
from agent_sec_perf.contracts.policy import Capability
from agent_sec_perf.contracts.tools import ToolSpec
from agent_sec_perf.foundation.errors import BenchError, ToolArgumentsInvalidError
from agent_sec_perf.harness.arguments import SubsetArgumentValidator
from agent_sec_perf.tools.files import ListDirTool, ReadFileTool, WriteFileTool
from agent_sec_perf.tools.shell import ShellCommandTool

#: 唯一 sentinel：出现在载荷里，但**不得**出现在异常消息中（``V4``：不回显不可信内容）。
_SENTINEL: Final = "SENTINEL-7f3a-not-for-logs"

#: ``ADR-0020`` §5.1 的白名单（测试侧独立抄写：机器检查的判据是**该 ADR**，不是模块实现）。
_OBJECT_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"type", "properties", "required", "additionalProperties", "description"}
)
_PROPERTY_KEYWORDS: Final[Mapping[str, frozenset[str]]] = {
    "string": frozenset({"type", "description", "minLength", "maxLength"}),
    "integer": frozenset(
        {"type", "description", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
    ),
    "number": frozenset(
        {"type", "description", "minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum"}
    ),
    "boolean": frozenset({"type", "description"}),
    "array": frozenset({"type", "description", "items", "minItems", "maxItems"}),
}
_ALLOWED_KEYWORDS: Final[frozenset[str]] = _OBJECT_KEYWORDS | frozenset().union(
    *_PROPERTY_KEYWORDS.values()
)

#: 明确**未支持**的关键字（出现即声明缺陷；逐类各留一条样本，见 ``ADR-0020`` §5.1 末）。
_UNSUPPORTED_KEYWORDS: Final[tuple[str, ...]] = (
    "enum",
    "const",
    "pattern",
    "format",
    "oneOf",
    "anyOf",
    "allOf",
    "not",
    "$ref",
    "$defs",
    "patternProperties",
    "propertyNames",
    "uniqueItems",
    "multipleOf",
    "title",
)

_OBJECT: Final[dict[str, object]] = {
    "type": "object",
    "properties": {},
    "additionalProperties": False,
}


class _NullSink:
    """``AuditSink`` 的替身：本文件只借它构造内置工具以取到 ``parameters_schema``。"""

    def emit(self, event: AuditEvent) -> None:
        del event

    def flush(self) -> None:
        return


def _spec(schema: Mapping[str, object], *, name: str = "probe") -> ToolSpec:
    """用给定 ``parameters_schema`` 造一个 ``ToolSpec``（校验器只读 schema）。"""
    return ToolSpec(
        name=name,
        description="测试用工具",
        parameters_schema=schema,
        capabilities=frozenset({Capability.READ_FILE}),
    )


def _schema(
    properties: Mapping[str, object], *, required: list[str] | None = None
) -> dict[str, object]:
    """造一个子集内的顶层对象 schema（``additionalProperties`` 显式 ``False``）。"""
    schema: dict[str, object] = {
        "type": "object",
        "properties": dict(properties),
        "additionalProperties": False,
    }
    if required is not None:
        schema["required"] = required
    return schema


def _reject(spec: ToolSpec, arguments_json: str) -> str:
    """断言被拒、消息是固定模板、且**不含** sentinel 与原始 JSON（``V4``）；返回消息。"""
    with pytest.raises(ToolArgumentsInvalidError) as excinfo:
        SubsetArgumentValidator().validate(spec=spec, arguments_json=arguments_json)
    message = str(excinfo.value)
    assert message.startswith("参数不合法："), f"错误消息必须符合固定模板：{message!r}"
    assert _SENTINEL not in message, f"V4：错误消息回显了参数值：{message!r}"
    if arguments_json:  # 空串是任何字符串的子串，跳过该条（本身就是"非法 JSON"用例）
        assert arguments_json not in message, f"V4：错误消息回显了原始 JSON：{message!r}"
    return message


def _accept(spec: ToolSpec, arguments_json: str) -> Mapping[str, object]:
    """断言通过并返回校验后的参数映射。"""
    return SubsetArgumentValidator().validate(spec=spec, arguments_json=arguments_json)


# ---------------------------------------------------------------------------
# H-3 / 正向：子集边界逐类
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_string_property_within_length_bounds_is_accepted() -> None:
    """字符串：长度落在 ``minLength``/``maxLength`` 内即通过（含端点）。"""
    spec = _spec(_schema({"path": {"type": "string", "minLength": 1, "maxLength": 3}}))

    assert dict(_accept(spec, '{"path": "ab"}')) == {"path": "ab"}
    assert dict(_accept(spec, '{"path": "a"}')) == {"path": "a"}
    assert dict(_accept(spec, '{"path": "abc"}')) == {"path": "abc"}


@pytest.mark.unit
def test_string_length_is_counted_in_unicode_code_points() -> None:
    """长度按 **Unicode 码点** 计（``ADR-0020`` §5.1）：一个合字算 1，不是 2 或 3。"""
    spec = _spec(_schema({"s": {"type": "string", "maxLength": 1}}))

    assert dict(_accept(spec, json.dumps({"s": "\u00e9"}))) == {"s": "\u00e9"}


@pytest.mark.unit
def test_integer_bounds_are_inclusive() -> None:
    """整数：``minimum`` / ``maximum`` 是**闭**区间（端点通过）。"""
    schema = _schema({"n": {"type": "integer", "minimum": 1, "maximum": 3}})
    spec = _spec(schema)

    assert dict(_accept(spec, '{"n": 1}')) == {"n": 1}
    assert dict(_accept(spec, '{"n": 3}')) == {"n": 3}


@pytest.mark.unit
def test_exclusive_bounds_are_open() -> None:
    """``exclusiveMinimum`` / ``exclusiveMaximum`` 是**开**区间（端点被拒）。"""
    schema = _schema({"n": {"type": "number", "exclusiveMinimum": 0, "exclusiveMaximum": 1}})
    spec = _spec(schema)

    assert dict(_accept(spec, '{"n": 0.5}')) == {"n": 0.5}
    _reject(spec, '{"n": 0}')
    _reject(spec, '{"n": 1}')


@pytest.mark.unit
def test_number_accepts_int_and_float() -> None:
    """``number`` 接受 ``int`` 与 ``float``（``ADR-0020`` §5.1）。"""
    spec = _spec(_schema({"x": {"type": "number"}}))

    assert dict(_accept(spec, '{"x": 3}')) == {"x": 3.0}
    assert dict(_accept(spec, '{"x": 3.5}')) == {"x": 3.5}


@pytest.mark.unit
def test_boolean_accepts_both_values() -> None:
    """``boolean`` 接受 ``true`` 与 ``false``（且只接受它们）。"""
    spec = _spec(_schema({"b": {"type": "boolean"}}))

    assert dict(_accept(spec, '{"b": true}')) == {"b": True}
    assert dict(_accept(spec, '{"b": false}')) == {"b": False}


@pytest.mark.unit
def test_array_items_and_length_bounds_are_enforced_on_the_positive_path() -> None:
    """数组：元素类型与长度边界都在范围内即通过。"""
    schema = _schema(
        {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3}}
    )
    spec = _spec(schema)

    assert dict(_accept(spec, '{"argv": ["ls"]}')) == {"argv": ["ls"]}
    assert dict(_accept(spec, '{"argv": ["ls", "-l", "-a"]}')) == {"argv": ["ls", "-l", "-a"]}


@pytest.mark.unit
def test_optional_property_may_be_absent_and_is_not_defaulted() -> None:
    """可选键缺席即通过，**且不补默认值**（``V2``：输出只含载荷中出现的已声明键）。"""
    schema = _schema(
        {"path": {"type": "string"}, "max_bytes": {"type": "integer"}}, required=["path"]
    )
    spec = _spec(schema)

    assert dict(_accept(spec, '{"path": "x"}')) == {"path": "x"}


@pytest.mark.unit
def test_empty_arguments_object_is_accepted_when_nothing_is_required() -> None:
    """``properties`` 为空且无必填 ⇒ ``{}`` 合法（不是"没有参数"即失败）。"""
    assert dict(_accept(_spec(_OBJECT), "{}")) == {}


# ---------------------------------------------------------------------------
# H-3 / H-11① 反向：子集边界逐类（类型不符）
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize(
    ("property_schema", "payload"),
    [
        ({"type": "string"}, {"k": 1}),
        ({"type": "string"}, {"k": None}),
        ({"type": "integer"}, {"k": "1"}),
        ({"type": "integer"}, {"k": 1.0}),
        ({"type": "number"}, {"k": "1.5"}),
        ({"type": "boolean"}, {"k": "true"}),
        ({"type": "boolean"}, {"k": 1}),
        ({"type": "array"}, {"k": "ls"}),
        ({"type": "array"}, {"k": {"0": "ls"}}),
    ],
)
def test_type_mismatch_is_rejected(
    property_schema: dict[str, object], payload: dict[str, object]
) -> None:
    """类型不符即拒绝（``V1``）。"""
    spec = _spec(_schema({"k": property_schema}))

    _reject(spec, json.dumps(payload))


@pytest.mark.unit
def test_type_mismatch_with_a_sentinel_value_is_not_echoed() -> None:
    """类型不符时错误消息只报"哪个键、期望什么类型"，**不回显值**（``V4``）。"""
    spec = _spec(_schema({"k": {"type": "integer"}}))

    message = _reject(spec, json.dumps({"k": _SENTINEL}))

    assert "k" in message
    assert "整数" in message


@pytest.mark.unit
def test_bool_is_not_an_integer() -> None:
    """``bool`` **不算整数**（``isinstance(True, int)`` 为真 ⇒ 必须显式排除）。"""
    spec = _spec(_schema({"n": {"type": "integer"}}))

    _reject(spec, '{"n": true}')
    _reject(spec, '{"n": false}')


@pytest.mark.unit
def test_bool_is_not_a_number() -> None:
    """``bool`` 也不算 ``number``。"""
    _reject(_spec(_schema({"x": {"type": "number"}})), '{"x": true}')


@pytest.mark.unit
@pytest.mark.parametrize(
    "arguments_json",
    ['{"s": ""}', '{"s": "abcd"}'],
)
def test_string_length_out_of_range_is_rejected(arguments_json: str) -> None:
    """字符串长度越界（下界与上界各一条）即拒绝。"""
    spec = _spec(_schema({"s": {"type": "string", "minLength": 1, "maxLength": 3}}))

    _reject(spec, arguments_json)


@pytest.mark.unit
@pytest.mark.parametrize(
    "arguments_json",
    ['{"n": 0}', '{"n": 4}', '{"n": -100}'],
)
def test_integer_bounds_are_enforced(arguments_json: str) -> None:
    """整数越界（低于 ``minimum`` / 高于 ``maximum``）即拒绝。"""
    spec = _spec(_schema({"n": {"type": "integer", "minimum": 1, "maximum": 3}}))

    _reject(spec, arguments_json)


@pytest.mark.unit
def test_exclusive_minimum_rejects_the_endpoint() -> None:
    """``exclusiveMinimum``（``run_command.timeout_s`` 用的正是它）拒绝端点 ``0``。"""
    spec = _spec(_schema({"timeout_s": {"type": "number", "exclusiveMinimum": 0}}))

    _reject(spec, '{"timeout_s": 0}')
    _reject(spec, '{"timeout_s": -1.5}')


@pytest.mark.unit
@pytest.mark.parametrize(
    "arguments_json",
    ['{"argv": []}', '{"argv": ["a", "b", "c", "d"]}', '{"argv": ["ok", 7]}'],
)
def test_array_length_and_item_type_violations_are_rejected(arguments_json: str) -> None:
    """数组：元素数量越界（下界 / 上界）与元素类型不符各一条。"""
    schema = _schema(
        {"argv": {"type": "array", "items": {"type": "string"}, "minItems": 1, "maxItems": 3}}
    )

    _reject(_spec(schema), arguments_json)


@pytest.mark.unit
def test_missing_required_key_is_rejected() -> None:
    """缺必填键（``required``）即拒绝（``V1``）。"""
    spec = _spec(_schema({"path": {"type": "string"}}, required=["path"]))

    message = _reject(spec, "{}")

    assert "path" in message
    assert "缺少必填键" in message


@pytest.mark.unit
def test_unknown_key_is_rejected() -> None:
    """未知键即拒绝（``additionalProperties: false`` 的语义，``V1``）。"""
    spec = _spec(_schema({"path": {"type": "string"}}))

    message = _reject(spec, json.dumps({"path": "x", "extra": 1}))

    assert "未声明的键" in message


@pytest.mark.unit
def test_unknown_key_name_and_value_are_not_echoed() -> None:
    """未知键的**名字**来自模型 ⇒ 不可信文本，**不得**回显（``I8`` / ``V4``）。"""
    spec = _spec(_schema({"path": {"type": "string"}}))

    message = _reject(spec, json.dumps({"path": "x", _SENTINEL: _SENTINEL}))

    assert _SENTINEL not in message


@pytest.mark.unit
def test_declared_key_name_is_echoed_after_sanitizing() -> None:
    """**已声明**的键名可以回显（它是"哪个键"唯一可用的表达），但必须净化（``V4``）。"""
    key = "a\u001b[2Jb"
    spec = _spec(_schema({key: {"type": "integer"}}))

    message = _reject(spec, json.dumps({key: "x"}))

    assert "\u001b" not in message, "控制字符必须被 sanitize_for_display 替换"
    assert "a?[2Jb" in message


@pytest.mark.unit
def test_declared_key_name_is_truncated_to_the_length_limit() -> None:
    """已声明键名回显前截断到上限（``ADR-0020`` §5.3 的 32）：超长键名不得整条进消息。"""
    key = "k" * 64
    spec = _spec(_schema({key: {"type": "integer"}}))

    message = _reject(spec, json.dumps({key: "x"}))

    assert key not in message
    assert ("k" * 32 + "…") in message


# ---------------------------------------------------------------------------
# H-3：非法 JSON / 体积上限 / 载荷不是对象
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("arguments_json", ["", "{", "not json", "{'path': 'x'}", "{}extra"])
def test_malformed_json_is_rejected(arguments_json: str) -> None:
    """非法 JSON 即拒绝（``V3``：一律 ``ToolArgumentsInvalidError``）。"""
    spec = _spec(_schema({"path": {"type": "string"}}))

    message = _reject(spec, arguments_json)

    assert "非法 JSON" in message


@pytest.mark.unit
def test_duplicate_object_key_is_rejected() -> None:
    """重复键即拒绝（``json.loads`` 默认静默取后者 ⇒ 模糊输入，``ADR-0020`` §5.2 步 1）。"""
    spec = _spec(_schema({"path": {"type": "string"}}))

    message = _reject(spec, '{"path": "a", "path": "b"}')

    assert "重复键" in message


@pytest.mark.unit
@pytest.mark.parametrize("arguments_json", ['{"x": NaN}', '{"x": Infinity}', '{"x": -Infinity}'])
def test_non_standard_numeric_constants_are_rejected(arguments_json: str) -> None:
    """``NaN`` / ``Infinity`` / ``-Infinity`` 是 Python ``json`` 的非标准扩展 ⇒ 拒绝。"""
    spec = _spec(_schema({"x": {"type": "number"}}))

    message = _reject(spec, arguments_json)

    assert "非法 JSON" in message


@pytest.mark.unit
def test_non_finite_number_via_overflow_is_rejected() -> None:
    """``1e400`` 会被解析成 ``inf``（**不**经过 ``parse_constant``）⇒ 由有限性检查拦住。"""
    spec = _spec(_schema({"x": {"type": "number"}}))

    message = _reject(spec, '{"x": 1e400}')

    assert "有限数值" in message


@pytest.mark.unit
def test_huge_integer_for_number_is_rejected_instead_of_overflowing() -> None:
    """任意精度大整数转 ``float`` 会溢出 ⇒ 必须收敛为拒绝，而不是抛 ``OverflowError``。"""
    spec = _spec(_schema({"x": {"type": "number"}}))

    _reject(spec, '{"x": ' + "9" * 400 + "}")


@pytest.mark.unit
@pytest.mark.parametrize("arguments_json", ["[]", '"text"', "42", "null", "true"])
def test_non_object_payload_is_rejected(arguments_json: str) -> None:
    """载荷不是 JSON 对象即拒绝（``ADR-0020`` §5.2 步 1）。"""
    message = _reject(_spec(_OBJECT), arguments_json)

    assert "必须是 JSON 对象" in message


@pytest.mark.unit
def test_oversized_char_count_is_rejected_without_parsing() -> None:
    """超字符数上限 ⇒ 直接拒绝（``V5``；在**解析之前**，故不可能是 JSON 错误）。"""
    spec = _spec(_schema({"path": {"type": "string"}}))
    payload = _json_with_string_length(65536 + 1)

    message = _reject(spec, payload)

    assert "输入超过体积上限" in message


@pytest.mark.unit
def test_oversized_bytes_are_rejected_without_parsing() -> None:
    """字符数在上限内但 **UTF-8 字节数**超限 ⇒ 同样拒绝（两条上限各自钉一条用例）。"""
    spec = _spec(_schema({"path": {"type": "string"}}))
    prefix, suffix = '{"path": "', '"}'
    payload = prefix + "\u00e9" * 40000 + suffix  # 40012 字符 / 80012 字节
    assert len(payload) <= 65536 < len(payload.encode("utf-8"))

    message = _reject(spec, payload)

    assert "输入超过体积上限" in message


@pytest.mark.unit
def test_a_payload_exactly_at_the_limit_is_still_validated() -> None:
    """恰好等于上限（不 ``>``）时**不**拒绝：上限是"超过即拒"，不是"达到即拒"。"""
    spec = _spec(_schema({"path": {"type": "string"}}))
    payload = _json_with_string_length(65536)

    assert set(_accept(spec, payload)) == {"path"}


def _json_with_string_length(total_length: int) -> str:
    """造一个总长恰好为 ``total_length`` 的合法 JSON 对象（ASCII ⇒ 字符数 == 字节数）。"""
    prefix, suffix = '{"path": "', '"}'
    padding = "a" * (total_length - len(prefix) - len(suffix))
    return f"{prefix}{padding}{suffix}"


# ---------------------------------------------------------------------------
# H-11②：子集之外的关键字 ⇒ 拒绝（不得静默忽略）
# ---------------------------------------------------------------------------


@pytest.mark.unit
@pytest.mark.parametrize("keyword", _UNSUPPORTED_KEYWORDS)
def test_unsupported_property_keyword_is_rejected(keyword: str) -> None:
    """属性 schema 里出现子集外的关键字 ⇒ ``ToolArgumentsInvalidError``（**不**静默忽略）。"""
    schema = _schema({"k": {"type": "string", keyword: "x"}})

    message = _reject(_spec(schema), '{"k": "ok"}')

    assert "工具参数声明缺陷" in message


@pytest.mark.unit
@pytest.mark.parametrize("keyword", ["enum", "pattern"])
def test_unsupported_keyword_rejects_even_when_the_payload_would_pass_it(keyword: str) -> None:
    """**元测试**：即使载荷对 ``enum`` / ``pattern`` 而言是"合法"的，也必须被拒。

    这一条证明守卫是**活的**（不是"因为载荷恰好不合法才报错"）：不存在"声明了但没校验"
    的静默路径——那正是 ``ADR-0020`` §5.1 判据要防的 fail-open 形状。
    """
    allowed_value = "a" if keyword == "enum" else "abc"
    schema = _schema(
        {"k": {"type": "string", keyword: [allowed_value] if keyword == "enum" else "^a"}}
    )
    spec = _spec(schema)

    _reject(spec, json.dumps({"k": allowed_value}))

    # 对照组：同一 schema 去掉该关键字后必须通过 —— 证明拒绝的原因是那个关键字本身。
    control = _schema({"k": {"type": "string"}})

    assert set(_accept(_spec(control), json.dumps({"k": allowed_value}))) == {"k"}


@pytest.mark.unit
@pytest.mark.parametrize(
    "schema",
    [
        {"type": "object", "properties": {}, "additionalProperties": False, "enum": []},
        {"type": "object", "properties": {}, "additionalProperties": False, "title": "T"},
        {"type": "object", "properties": {}},
        {"type": "object", "properties": {}, "additionalProperties": True},
        {"properties": {}, "additionalProperties": False},
        {"type": "array", "properties": {}, "additionalProperties": False},
        {"type": "object", "properties": [], "additionalProperties": False},
        {"type": "object", "properties": {"k": {"type": "object"}}, "additionalProperties": False},
        {"type": "object", "properties": {"k": {"type": "null"}}, "additionalProperties": False},
        {
            "type": "object",
            "properties": {"k": {"type": "string", "$ref": "#/x"}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"k": {"type": "array", "items": []}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"k": {"type": "string", "minLength": -1}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"k": {"type": "integer", "minimum": True}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {},
            "required": ["missing"],
            "additionalProperties": False,
        },
        {"type": "object", "properties": {}, "required": "path", "additionalProperties": False},
        {
            "type": "object",
            "properties": {"k": {"type": "string", "minLength": 5, "maxLength": 2}},
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {"k": {"type": "array", "minItems": 3, "maxItems": 1}},
            "additionalProperties": False,
        },
        "not-a-schema",
        [],
    ],
)
def test_schema_shape_defects_are_rejected(schema: object) -> None:
    """schema 形状缺陷（缺 ``additionalProperties`` / 子集外关键字 / 非法边界 …）一律拒绝。

    声明缺陷必须在**同一处**被发现与拒绝：否则"我们写错了 schema"会以"模型的参数不合法"
    的形态出现在审计里（``ADR-0020`` §5.2 步 2 与 §5.3 末的如实登记）。
    """
    spec = _spec(schema)  # type: ignore[arg-type]

    message = _reject(spec, "{}")

    assert "工具参数声明缺陷" in message


# ---------------------------------------------------------------------------
# V2 / 确定性 / V6（无状态、纯函数式）
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_output_is_a_new_mapping_with_only_declared_keys() -> None:
    """输出是**新**映射，只含 schema 声明过的键（``V2``）。"""
    schema = _schema(
        {"path": {"type": "string"}, "max_bytes": {"type": "integer"}}, required=["path"]
    )
    spec = _spec(schema)

    result = _accept(spec, '{"path": "x", "max_bytes": 4}')

    assert isinstance(result, dict)
    assert set(result) == {"path", "max_bytes"}


@pytest.mark.unit
def test_only_declared_keys_of_the_payload_are_returned() -> None:
    """载荷里**已声明**、但 schema 未声明的键不可能通过（未知键先被拒）；此处钉"输出不含额外键"。"""
    spec = _spec(_schema({"a": {"type": "string"}}))

    assert dict(_accept(spec, '{"a": "x"}')) == {"a": "x"}


@pytest.mark.unit
def test_validation_is_deterministic_and_order_independent() -> None:
    """同一（schema, 载荷）必得同一消息；且按 ``properties`` 键名升序报**第一条**错误。"""
    schema = _schema({"b": {"type": "integer"}, "a": {"type": "integer"}})
    spec = _spec(schema)

    first = _reject(spec, '{"b": "x", "a": "y"}')
    second = _reject(spec, '{"a": "y", "b": "x"}')

    assert first == second
    assert "a（整数）" in first, "必须按键名升序报第一条：a 在 b 之前"
    assert "b（" not in first


@pytest.mark.unit
def test_repeated_calls_do_not_depend_on_prior_calls() -> None:
    """``V6``：不缓存跨调用结果——先失败的调用不得影响后续调用。"""
    good = _spec(_schema({"path": {"type": "string"}}, required=["path"]))
    bad = _spec(_schema({"n": {"type": "integer"}}))
    validator = SubsetArgumentValidator()

    _reject(bad, json.dumps({"n": _SENTINEL}))
    assert dict(validator.validate(spec=good, arguments_json='{"path": "x"}')) == {"path": "x"}


@pytest.mark.unit
def test_validator_holds_no_instance_state() -> None:
    """``V6``：实例上没有可变状态（无缓存 / 无计数器）⇒ 可并发复用同一实例。"""
    assert vars(SubsetArgumentValidator()) == {}


@pytest.mark.unit
def test_failure_type_is_the_contract_error() -> None:
    """``V3``：失败类型是 ``foundation.errors.ToolArgumentsInvalidError``（``BenchError`` 子类）。"""
    assert issubclass(ToolArgumentsInvalidError, BenchError)
    with pytest.raises(ToolArgumentsInvalidError):
        SubsetArgumentValidator().validate(spec=_spec(_OBJECT), arguments_json="{")


@pytest.mark.unit
def test_validation_does_not_mutate_the_schema_or_the_payload_document() -> None:
    """纯函数：校验不修改 ``parameters_schema``（``ToolSpec`` 是不可变契约，schema 亦不得被就地改）。"""
    schema = _schema({"k": {"type": "string"}}, required=["k"])
    spec = _spec(schema)

    _accept(spec, '{"k": "x"}')

    assert schema == {
        "type": "object",
        "properties": {"k": {"type": "string"}},
        "required": ["k"],
        "additionalProperties": False,
    }


# ---------------------------------------------------------------------------
# H-11③：机器检查——内置工具的 schema 关键字集合 ⊆ ADR-0020 §5.1 的白名单
# ---------------------------------------------------------------------------


def _builtin_specs() -> dict[str, ToolSpec]:
    """四个内置工具的 ``ToolSpec``（构造只需一个 sink 替身）。"""
    sink = _NullSink()
    return {
        tool.spec.name: tool.spec
        for tool in (
            ReadFileTool(sink),
            WriteFileTool(sink),
            ListDirTool(sink),
            ShellCommandTool(sink),
        )
    }


def _collect_keywords(schema: object) -> set[str]:
    """递归收集 schema 里出现的**关键字**（跳过 ``properties`` 的键名——那是参数名）。"""
    if not isinstance(schema, Mapping):
        return set()
    found = {key for key in schema if isinstance(key, str)}
    properties = schema.get("properties")
    if isinstance(properties, Mapping):
        for value in properties.values():
            found |= _collect_keywords(value)
    found |= _collect_keywords(schema.get("items"))
    return found


@pytest.mark.unit
def test_builtin_tool_schemas_use_only_whitelisted_keywords() -> None:
    """``H-11``③：四个内置工具的 ``parameters_schema`` 关键字集合 ⊆ §5.1 白名单。

    这是"子集白名单被悄悄放宽"的反向守卫：真正的判据是**内置工具真的落在子集内**
    （而不是"校验器声称支持"）。
    """
    offenders: dict[str, list[str]] = {}
    for name, spec in _builtin_specs().items():
        extra = sorted(_collect_keywords(spec.parameters_schema) - _ALLOWED_KEYWORDS)
        if extra:
            offenders[name] = extra

    assert offenders == {}, f"内置工具的 schema 用了子集外的关键字（ADR-0020 §5.1）：{offenders}"


@pytest.mark.unit
def test_builtin_tool_schemas_do_not_use_known_unsupported_keywords() -> None:
    """同上，取**未支持清单**一侧再钉一次（防"白名单被子集外关键字污染"这种写法走偏）。"""
    offenders: dict[str, list[str]] = {}
    for name, spec in _builtin_specs().items():
        used = _collect_keywords(spec.parameters_schema) & set(_UNSUPPORTED_KEYWORDS)
        if used:
            offenders[name] = sorted(used)

    assert offenders == {}


@pytest.mark.unit
def test_the_keyword_collector_can_actually_fire() -> None:
    """元测试：收集器必须能**下探到属性 schema**，否则上面两条会空集通过。"""
    nested = {
        "type": "object",
        "properties": {"k": {"type": "string", "enum": ["a"]}},
        "additionalProperties": False,
    }

    collected = _collect_keywords(nested)

    assert "enum" in collected
    assert "enum" not in _ALLOWED_KEYWORDS
    assert "k" not in collected, "参数名不是关键字，收集器不得把它算进来"


@pytest.mark.unit
@pytest.mark.parametrize(
    ("tool_name", "payload"),
    [
        ("read_file", {"path": "x"}),
        ("write_file", {"path": "x", "content": ""}),
        ("list_dir", {"path": "x"}),
        ("run_command", {"argv": ["ls"]}),
    ],
)
def test_builtin_tool_schemas_are_accepted_by_the_validator(
    tool_name: str, payload: dict[str, object]
) -> None:
    """四个内置工具的 schema 必须能被校验器**接受**（不只是"关键字集合看起来对"）。"""
    spec = _builtin_specs()[tool_name]

    assert dict(_accept(spec, json.dumps(payload))) == payload
