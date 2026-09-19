"""工具参数的信任边界校验器：手写 JSON-Schema 子集实现（L3 编排层的叶子模块）。

契约：``docs/design/interfaces/harness.md`` §3.4 的 ``V1``~``V6``。
实现细节（受支持子集 / 解析与校验顺序 / 错误消息形状 / 上限常量）**以**
``docs/adr/0020-argument-validator-implementation.md`` §5 为准，本模块是它的唯一实现。

为什么是手写子集而不是 pydantic / ``jsonschema``（论证见 ``ADR-0020`` §3~§5）：

* ``V4``（**错误文本不得回显不可信内容**）必须是**结构性质**而不是纪律。本模块的错误消息
  由唯一的格式化器 :func:`_invalid` 产生：不含任何来自载荷的值或片段，未声明的键名永不出现。
  用库（pydantic 的 ``str(e)``、``jsonschema`` 的错误渲染）默认会回显 ``input_value``，
  省下的代码正好被"重写库的错误输出"抵掉（``ADR-0020`` §1.3 的实测证据）。
* **零新增依赖、零编译扩展**：对低资源 / 可移植性（``ADR-0015`` §8.2.1 的 ``V-j``）净零。

两条与安全直接相关的取舍（写清理由，避免后人改回）：

1. **子集之外的校验关键字一律拒绝**（不静默忽略）。schema 是**我方生成的声明**
   （``ADR-0020`` §5.1 的判据），出现不支持的关键字必然是声明缺陷；忽略它等于让
   "声明了但没校验"静默存在——fail-open 的形状。
2. **``additionalProperties`` 必须显式为 ``false``**：JSON Schema 的默认语义是"允许未知键"，
   而 ``V1`` 要求未知键拒绝。把"缺失"静默当作 ``false``，本模块的语义就不再是标准
   JSON Schema，却仍以 JSON Schema 的名义被读写——"同一事实两处表述"的种子。
   要求显式写出 ⇒ 语义与标准一致，且四个内置工具本就都写着 ``False``（零改动）。

错误消息形状（``ADR-0020`` §5.3）：固定模板 ``参数不合法：<键名>（<期望>）``。
``<键名>`` 只可能是 ``properties`` **已声明**的键名，且经
``sanitize_for_display(limit=32)`` 净化与截断；**未声明（未知）的键名永不回显**——它是
模型自带的不可信文本（``harness.md`` §2.2 的 ``I8`` 澄清：模型可把 ``{"\\u001b[2J…": 1}``
当作未知键，回显即把攻击者可控字节送进一个"干净字段"，甚至终端控制序列）。
当失败不归属于某个已声明参数（体积超限 / JSON 非法 / 声明缺陷 / 未声明的键）时，
``<键名>`` 槽位**留空**（模板形状不变，信息量为零的槽位比"编一个名字"更安全）。

依赖：``contracts`` + ``foundation.errors`` + ``foundation.logging``（契约 §3.1 的
``arguments`` 段）。**不** import 任何 harness 兄弟模块（``H2``），也**不** import
``model`` / ``tools`` / ``security`` / ``observability`` / ``cli`` 的实现（``H1``）。
无状态、纯函数式（``V6``）：不读时钟 / 环境 / 文件 / 网络，无实例可变状态，不缓存跨调用结果。
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Final, Never, cast

from agent_sec_perf.contracts.tools import ToolSpec
from agent_sec_perf.foundation.errors import ToolArgumentsInvalidError
from agent_sec_perf.foundation.logging import sanitize_for_display

__all__ = ["SubsetArgumentValidator"]

#: ``arguments_json`` 的体积上限（``V5``；与 ``tools/registry.py::MAX_TOOL_OUTPUT_BYTES`` 同量级）。
_MAX_CHARS: Final = 65536
_MAX_BYTES: Final = 65536

#: 键名回显进错误消息前的截断长度（不可信文本；``ADR-0020`` §5.3）。
_KEY_LIMIT: Final = 32

#: 顶层（对象）schema 允许的关键字（``ADR-0020`` §5.1 的封闭清单）。
_OBJECT_KEYWORDS: Final[frozenset[str]] = frozenset(
    {"type", "properties", "required", "additionalProperties", "description"}
)

#: 属性 schema 允许的关键字，按其 ``type`` 分列（``ADR-0020`` §5.1 的封闭清单；
#: ``description`` 是注解，允许且忽略）。
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

# --- 期望文案（我方常量；``_invalid`` 是模块内唯一的消息来源，``V4`` 的结构性质）---------

_EXPECT_OVERSIZE: Final = "输入超过体积上限"
_EXPECT_JSON: Final = "非法 JSON"
_EXPECT_DUPLICATE_KEY: Final = "非法 JSON：出现重复键"
_EXPECT_JSON_OBJECT: Final = "必须是 JSON 对象"
_EXPECT_UNKNOWN_KEY: Final = "未声明的键"
_EXPECT_REQUIRED: Final = "缺少必填键"
_EXPECT_STRING: Final = "字符串"
_EXPECT_NON_EMPTY_STRING: Final = "非空字符串"
_EXPECT_INTEGER: Final = "整数"
_EXPECT_NUMBER: Final = "有限数值"
_EXPECT_BOOLEAN: Final = "布尔值"
_EXPECT_ARRAY: Final = "数组"
_EXPECT_STRING_ARRAY: Final = "字符串数组"
_EXPECT_RANGE: Final = "取值超出声明范围"
_EXPECT_LENGTH: Final = "长度超出声明范围"
_EXPECT_ITEM_COUNT: Final = "元素数量超出声明范围"


class _MalformedJsonError(ValueError):
    """``json.loads`` 的护栏触发（非标准常量）：内部标记，只在本模块内流转。

    继承 ``ValueError`` 以便与 ``json.JSONDecodeError`` 一起被同一条 ``except`` 收敛为
    ``ToolArgumentsInvalidError``（``V3``：所有失败都是同一异常类型）。
    """


class _DuplicateKeyError(_MalformedJsonError):
    """对象里出现重复键（``json.loads`` 默认**静默取后者** ⇒ 是模糊输入）。"""


@dataclass(frozen=True)
class _PropertySchema:
    """属性 schema 的**已校验**内部表示（``ADR-0020`` §5.1 子集内）。

    ``kind`` 取值由 :func:`_parse_property_schema` 限定为
    ``string`` / ``integer`` / ``number`` / ``boolean`` / ``array`` 之一。
    """

    kind: str
    minimum: float | None = None
    maximum: float | None = None
    exclusive_minimum: float | None = None
    exclusive_maximum: float | None = None
    min_length: int | None = None
    max_length: int | None = None
    min_items: int | None = None
    max_items: int | None = None
    items: _PropertySchema | None = None


@dataclass(frozen=True)
class _ObjectSchema:
    """顶层（对象）schema 的**已校验**内部表示。"""

    properties: Mapping[str, _PropertySchema]
    required: tuple[str, ...]


class SubsetArgumentValidator:
    """``harness.md`` §3.4 的 ``ArgumentValidator`` 的最小子集实现（无状态、纯函数式）。

    受支持子集 / 解析与校验顺序 / 错误消息形状见 ``ADR-0020`` §5；本类是
    ``harness/arguments.py`` 的**唯一公开接口**。
    """

    def validate(self, *, spec: ToolSpec, arguments_json: str) -> Mapping[str, object]:
        """按 ``spec.parameters_schema`` **严格**校验原始 JSON 文本（``V1``~``V6``）。

        Args:
            spec: 工具描述；``parameters_schema`` 必须是 ``ADR-0020`` §5.1 子集内的合法 shape。
            arguments_json: 模型给出的**原始 JSON 文本**（不可信；除本方法外不得解析它）。

        Returns:
            **新**字典，只含 schema 声明过、且载荷中出现的键（``V2``；不补可选键的默认值）。

        Raises:
            ToolArgumentsInvalidError: 体积超限 / JSON 非法（含重复键与非有限常量）/ 载荷不是
                对象 / schema 声明缺陷 / 出现未声明的键 / 缺必填键 / 类型不符 / 取值或长度
                超出声明范围。错误消息**不回显**任何参数值，也不回显未声明的键名（``V4``）。
        """
        _check_size(arguments_json)
        payload = _parse_json_object(arguments_json)
        schema = _parse_object_schema(spec.parameters_schema)
        _reject_unknown_keys(payload, schema)
        _require_present_keys(payload, schema)
        return _validated_arguments(payload, schema)


# ---------------------------------------------------------------------------
# 错误消息：唯一来源（V4 的结构性质）
# ---------------------------------------------------------------------------


def _invalid(*, key: str | None, reason: str) -> ToolArgumentsInvalidError:
    """构造错误异常；``key is None`` ⇒ ``<键名>`` 槽位留空。

    这是模块内**唯一**产生错误消息的地方：``reason`` 一律是我方常量（见 ``_EXPECT_*``），
    ``key`` 只可能是 schema 已声明的键名且经净化截断。⇒ "不回显不可信内容"由代码结构决定，
    不依赖调用点记得过滤（``V4``）。
    """
    shown = "" if key is None else sanitize_for_display(key, limit=_KEY_LIMIT)
    return ToolArgumentsInvalidError(f"参数不合法：{shown}（{reason}）")


def _defect(reason: str) -> ToolArgumentsInvalidError:
    """声明缺陷的异常（``ADR-0020`` §5.2 步 2）。

    ⚠️ 如实登记：契约 §2.7 的 ``denied_reason`` 是闭集，其中**没有**"工具声明缺陷"这一档
    ⇒ 它与"模型给的参数不合法"在审计里**同形**（都落到 ``invalid_arguments``）。
    见 ``ADR-0020`` §5.3 末段；**不**在本模块代改契约。
    """
    return _invalid(key=None, reason=f"工具参数声明缺陷：{reason}")


# ---------------------------------------------------------------------------
# 步 0：体积检查（在解析之前；V5）
# ---------------------------------------------------------------------------


def _check_size(arguments_json: str) -> None:
    """超过字符数或 UTF-8 字节数上限即拒绝（**不尝试解析**）。

    字符数检查在前是"最便宜的早退"（``ADR-0020`` §5.2 步 0）。
    ``errors="surrogatepass"``：含孤立代理项的字符串（无法按 UTF-8 编码）不得在本步抛
    ``UnicodeEncodeError``——``V3`` 要求所有失败都是 ``ToolArgumentsInvalidError``；
    它对这类本来就非法的输入给出**偏大**的字节计数 ⇒ 只会更早拒绝，方向是 fail-secure。
    """
    if len(arguments_json) > _MAX_CHARS:
        raise _invalid(key=None, reason=_EXPECT_OVERSIZE)
    if len(arguments_json.encode("utf-8", errors="surrogatepass")) > _MAX_BYTES:
        raise _invalid(key=None, reason=_EXPECT_OVERSIZE)


# ---------------------------------------------------------------------------
# 步 1：解析（三个护栏；V5 / V1 的前提）
# ---------------------------------------------------------------------------


def _reject_constant(token: str) -> Never:
    """``parse_constant`` 护栏：拒绝 ``NaN`` / ``Infinity`` / ``-Infinity``。

    Python ``json`` 的非标准扩展。``inf`` 参与数值比较会让"必须有上限"的约束失去意义
    （``inf`` 超时 = 没有超时），故一律视为非法输入。
    """
    del token  # 只复用固定的拒绝文案，不回显输入片段
    raise _MalformedJsonError("non-finite numeric constant")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    """``object_pairs_hook`` 护栏：出现重复键即拒绝。

    ``json.loads`` 默认静默取后者；"同一个键两个值"在安全语义上是模糊输入——
    校验器与下游工具可能拿到同一个对象的不同解释面（``ADR-0020`` §5.2 步 1）。
    """
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError("duplicate object key")
        result[key] = value
    return result


def _parse_json_object(arguments_json: str) -> Mapping[str, object]:
    """解析为 JSON 对象；任何失败都收敛为 ``ToolArgumentsInvalidError``。"""
    try:
        payload: object = json.loads(
            arguments_json,
            parse_constant=_reject_constant,
            object_pairs_hook=_reject_duplicate_keys,
        )
    except _DuplicateKeyError as exc:
        raise _invalid(key=None, reason=_EXPECT_DUPLICATE_KEY) from exc
    except (ValueError, RecursionError) as exc:
        # ``RecursionError``：``json`` 的扫描器对**深度嵌套**的输入会抛它（不属 ``ValueError``）。
        # 这类输入同样"不是我们能校验的 JSON 对象" ⇒ 与非法 JSON 同处置，**不得**逃逸成
        # 非契约异常（``V3``）。
        raise _invalid(key=None, reason=_EXPECT_JSON) from exc
    if not isinstance(payload, dict):
        raise _invalid(key=None, reason=_EXPECT_JSON_OBJECT)
    return cast("dict[str, object]", payload)


# ---------------------------------------------------------------------------
# 步 2：schema 形状检查（声明缺陷在此处被发现；子集外关键字一律拒绝）
# ---------------------------------------------------------------------------


def _as_mapping(value: object, *, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise _defect(f"{what} 必须是映射")
    return value


def _reject_extra_keywords(table: Mapping[str, object], allowed: frozenset[str]) -> None:
    """出现白名单之外的关键字即拒绝（**不回显**关键字名与键名，见模块 docstring）。"""
    if set(table) - allowed:
        raise _defect("含子集之外的校验关键字")


def _parse_object_schema(schema: object) -> _ObjectSchema:
    """校验 ``parameters_schema`` 落在子集内，并返回其内部表示。"""
    table = _as_mapping(schema, what="工具参数 schema")
    _reject_extra_keywords(table, _OBJECT_KEYWORDS)
    if table.get("type") != "object":
        raise _defect("顶层 type 必须是 object")

    raw_properties = table.get("properties")
    if not isinstance(raw_properties, Mapping):
        raise _defect("properties 必须是映射")
    properties: dict[str, _PropertySchema] = {}
    for name, raw in raw_properties.items():
        if not isinstance(name, str):
            raise _defect("properties 的键必须是字符串")
        properties[name] = _parse_property_schema(raw)

    required = _parse_required(table, properties)

    if table.get("additionalProperties") is not False:
        raise _defect("additionalProperties 必须显式为 false")
    return _ObjectSchema(properties=properties, required=required)


def _parse_required(
    table: Mapping[str, object], properties: Mapping[str, _PropertySchema]
) -> tuple[str, ...]:
    if "required" not in table:
        return ()
    raw = table["required"]
    if not isinstance(raw, list):
        raise _defect("required 必须是字符串列表")
    names: list[str] = []
    for name in raw:
        if not isinstance(name, str) or name not in properties:
            raise _defect("required 的每一项都必须是 properties 中已声明的键名")
        names.append(name)
    return tuple(names)


def _parse_property_schema(schema: object) -> _PropertySchema:
    table = _as_mapping(schema, what="属性 schema")
    kind = table.get("type")
    if not isinstance(kind, str) or kind not in _PROPERTY_KEYWORDS:
        raise _defect("属性 type 不是受支持子集内的取值")
    _reject_extra_keywords(table, _PROPERTY_KEYWORDS[kind])

    if kind == "string":
        return _parse_string_schema(table)
    if kind in ("integer", "number"):
        return _parse_numeric_schema(table, kind)
    if kind == "array":
        return _parse_array_schema(table)
    return _PropertySchema(kind=kind)


def _parse_string_schema(table: Mapping[str, object]) -> _PropertySchema:
    min_length = _optional_non_negative_int(table, "minLength")
    max_length = _optional_non_negative_int(table, "maxLength")
    if min_length is not None and max_length is not None and min_length > max_length:
        raise _defect("minLength 不得大于 maxLength")
    return _PropertySchema(kind="string", min_length=min_length, max_length=max_length)


def _parse_numeric_schema(table: Mapping[str, object], kind: str) -> _PropertySchema:
    return _PropertySchema(
        kind=kind,
        minimum=_optional_finite_number(table, "minimum"),
        maximum=_optional_finite_number(table, "maximum"),
        exclusive_minimum=_optional_finite_number(table, "exclusiveMinimum"),
        exclusive_maximum=_optional_finite_number(table, "exclusiveMaximum"),
    )


def _parse_array_schema(table: Mapping[str, object]) -> _PropertySchema:
    min_items = _optional_non_negative_int(table, "minItems")
    max_items = _optional_non_negative_int(table, "maxItems")
    if min_items is not None and max_items is not None and min_items > max_items:
        raise _defect("minItems 不得大于 maxItems")
    # ``items`` 是**单个**属性 schema（不支持元组式 ``items``）；缺省 ⇒ 只校验数组本身
    # （元素类型未声明 ⇒ 不校验元素；``ADR-0020`` §5.1 未把 ``items`` 列为必需）。
    items = _parse_property_schema(table["items"]) if "items" in table else None
    return _PropertySchema(kind="array", min_items=min_items, max_items=max_items, items=items)


def _optional_non_negative_int(table: Mapping[str, object], keyword: str) -> int | None:
    """取可选的非负整数关键字；``bool`` 显式排除（``True`` 是 ``int`` 的实例）。"""
    value = table.get(keyword)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise _defect(f"{keyword} 必须是非负整数")
    return value


def _optional_finite_number(table: Mapping[str, object], keyword: str) -> float | None:
    """取可选的有限数值关键字；``bool`` 与非有限值一律拒绝。"""
    value = table.get(keyword)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _defect(f"{keyword} 必须是数值")
    number = float(value)
    if not math.isfinite(number):
        raise _defect(f"{keyword} 必须是有限数值")
    return number


# ---------------------------------------------------------------------------
# 步 3~6：载荷校验与输出（V1 / V2）
# ---------------------------------------------------------------------------


def _reject_unknown_keys(payload: Mapping[str, object], schema: _ObjectSchema) -> None:
    """``additionalProperties: false`` 的语义（``V1``）；**不回显**未知键名（``V4``/``I8``）。"""
    if set(payload) - set(schema.properties):
        raise _invalid(key=None, reason=_EXPECT_UNKNOWN_KEY)


def _require_present_keys(payload: Mapping[str, object], schema: _ObjectSchema) -> None:
    """缺必填即拒绝（``V1``）；键名是 schema 已声明的，故可回显。"""
    for name in schema.required:
        if name not in payload:
            raise _invalid(key=name, reason=_EXPECT_REQUIRED)


def _validated_arguments(
    payload: Mapping[str, object], schema: _ObjectSchema
) -> Mapping[str, object]:
    """按 ``properties`` **键名升序**逐属性校验，输出只含声明过的键（``V2``）。

    升序是写死的：同一输入必得同一错误消息（确定性是可回放的前提）。
    不补可选键的默认值——"缺省"与"显式给了默认值"在下游是两件事。
    """
    validated: dict[str, object] = {}
    for name in sorted(schema.properties):
        if name not in payload:
            continue
        validated[name] = _validate_value(name, payload[name], schema.properties[name])
    return validated


def _type_label(prop: _PropertySchema) -> str:
    """类型不符时的期望文案（我方常量，按声明类型选取）。"""
    if prop.kind == "string":
        return _EXPECT_NON_EMPTY_STRING if (prop.min_length or 0) >= 1 else _EXPECT_STRING
    if prop.kind == "integer":
        return _EXPECT_INTEGER
    if prop.kind == "number":
        return _EXPECT_NUMBER
    if prop.kind == "boolean":
        return _EXPECT_BOOLEAN
    if prop.items is not None and prop.items.kind == "string":
        return _EXPECT_STRING_ARRAY
    return _EXPECT_ARRAY


def _validate_value(key: str, value: object, prop: _PropertySchema) -> object:
    if prop.kind == "string":
        return _validate_string(key, value, prop)
    if prop.kind == "integer":
        return _validate_integer(key, value, prop)
    if prop.kind == "number":
        return _validate_number(key, value, prop)
    if prop.kind == "boolean":
        if not isinstance(value, bool):
            raise _invalid(key=key, reason=_EXPECT_BOOLEAN)
        return value
    if prop.kind == "array":
        return _validate_array(key, value, prop)
    # ``kind`` 已由 ``_parse_property_schema`` 限定；此处不可达（防御性，不是用户路径）。
    raise _defect("属性 type 不是受支持子集内的取值")


def _validate_string(key: str, value: object, prop: _PropertySchema) -> str:
    if not isinstance(value, str):
        raise _invalid(key=key, reason=_type_label(prop))
    length = len(value)  # Python ``str`` 的 ``len`` 即 Unicode 码点数（ADR §5.1 的口径）
    if prop.min_length is not None and length < prop.min_length:
        raise _invalid(key=key, reason=_EXPECT_LENGTH)
    if prop.max_length is not None and length > prop.max_length:
        raise _invalid(key=key, reason=_EXPECT_LENGTH)
    return value


def _validate_integer(key: str, value: object, prop: _PropertySchema) -> int:
    """整数：``bool`` 显式排除（``True`` 是 ``int`` 的子类，不挡会被当成 1）。

    与 ``tools/registry.py::optional_positive_int_arg`` 同款教训（``ADR-0020`` §6 的风险
    缓解条）；也刻意**不**接受 ``3.0`` 这类"整数值的浮点数"——工具侧的载荷校验就是
    ``isinstance(value, int)``，两处口径必须一致，否则非法值会以更晚、更隐蔽的形态出现。
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise _invalid(key=key, reason=_type_label(prop))
    _check_bounds(key, value, prop)
    return value


def _validate_number(key: str, value: object, prop: _PropertySchema) -> float:
    """数值：接受 ``int`` / ``float``，排除 ``bool``；必须是有限值。

    任意精度整数（``json.loads`` 可产出）转 ``float`` 会溢出 ⇒ 用 ``OverflowError`` 收敛为拒绝
    （与 ``inf`` 同处置：一个超出可表示范围的"超时"没有意义）。
    """
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _invalid(key=key, reason=_type_label(prop))
    try:
        number = float(value)
    except OverflowError as exc:
        raise _invalid(key=key, reason=_type_label(prop)) from exc
    if not math.isfinite(number):
        raise _invalid(key=key, reason=_type_label(prop))
    _check_bounds(key, number, prop)
    return number


def _check_bounds(key: str, value: int | float, prop: _PropertySchema) -> None:
    """数值边界（``minimum`` / ``maximum`` / ``exclusive*``）。

    ``int`` 与 ``float`` 的比较在 Python 里是**精确**的（不会溢出、不损失精度），
    因此大整数与浮点边界的比较仍然可靠。
    """
    if prop.minimum is not None and value < prop.minimum:
        raise _invalid(key=key, reason=_EXPECT_RANGE)
    if prop.maximum is not None and value > prop.maximum:
        raise _invalid(key=key, reason=_EXPECT_RANGE)
    if prop.exclusive_minimum is not None and value <= prop.exclusive_minimum:
        raise _invalid(key=key, reason=_EXPECT_RANGE)
    if prop.exclusive_maximum is not None and value >= prop.exclusive_maximum:
        raise _invalid(key=key, reason=_EXPECT_RANGE)


def _validate_array(key: str, value: object, prop: _PropertySchema) -> list[object]:
    if not isinstance(value, list):
        raise _invalid(key=key, reason=_type_label(prop))
    if prop.min_items is not None and len(value) < prop.min_items:
        raise _invalid(key=key, reason=_EXPECT_ITEM_COUNT)
    if prop.max_items is not None and len(value) > prop.max_items:
        raise _invalid(key=key, reason=_EXPECT_ITEM_COUNT)
    if prop.items is not None:
        items = prop.items
        for element in value:
            _validate_value(key, element, items)
    return value
