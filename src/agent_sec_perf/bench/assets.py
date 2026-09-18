"""资产清单解析：sha256 的**唯一真源**。

清单文件（``.ide/assets/models.txt``）由构建期脚本与运行期校验共用。
这里刻意**不**把摘要值抄进代码或文档——抄写会在清单更新后产生
"文档与事实分叉"（``docs/engineering/post-build-checklist.md`` §6.3）。
"""

from __future__ import annotations

import hashlib
import pathlib
from dataclasses import dataclass

from agent_sec_perf.foundation.errors import ProtocolError

DEFAULT_MODELS_MANIFEST = pathlib.Path(".ide/assets/models.txt")
_HASH_CHUNK_BYTES = 1024 * 1024


@dataclass(frozen=True)
class ModelAsset:
    """清单中的一个模型条目。"""

    filename: str
    sha256: str
    size: str
    quant: str


def read_model_manifest(path: pathlib.Path) -> dict[str, ModelAsset]:
    """解析模型清单，返回 ``文件名 → 条目`` 的映射。

    Args:
        path: 清单文件路径。

    Raises:
        ProtocolError: 文件缺失，或存在字段不足、摘要格式非法的行。
    """
    if not path.is_file():
        msg = f"模型清单不存在：{path}"
        raise ProtocolError(msg)
    assets: dict[str, ModelAsset] = {}
    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 4:
            msg = f"{path}:{lineno} 字段不足（应为 <sha256>|<文件名>|<URL>|<量化档>|...）"
            raise ProtocolError(msg)
        digest, filename, _url, quant = fields[0], fields[1], fields[2], fields[3]
        size = fields[4] if len(fields) > 4 else "unknown"
        if len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            msg = f"{path}:{lineno} 摘要格式非法：{digest[:16]}…"
            raise ProtocolError(msg)
        assets[filename] = ModelAsset(filename=filename, sha256=digest, size=size, quant=quant)
    if not assets:
        msg = f"{path} 未解析出任何模型条目"
        raise ProtocolError(msg)
    return assets


def sha256_of(path: pathlib.Path) -> str:
    """计算文件的 sha256（流式读取，避免把 GB 级权重读进内存）。

    这是"运行期可选复查"用的；构建期已由 ``fetch-assets.sh verify`` 校验过一次，
    因此默认不每轮重算，以免每晚多花一两分钟只做同一件事。
    """
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(_HASH_CHUNK_BYTES):
            digest.update(chunk)
    return digest.hexdigest()


__all__ = ["DEFAULT_MODELS_MANIFEST", "ModelAsset", "read_model_manifest", "sha256_of"]
