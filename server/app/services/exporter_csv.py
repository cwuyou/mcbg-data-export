"""CSV 流式写入：
- 优先走 pyarrow 原生 CSV writer（C 扩展，释放 GIL，对长字符串字段吞吐远高于 Python csv 模块）
- 不可用时回退到 Python csv + 行级迭代
"""
from __future__ import annotations

import csv
import io
from typing import Iterator, List, Any, Callable, Optional

from .reader import ReaderContext, prefetch


UTF8_BOM = b"\xef\xbb\xbf"


def _format_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    if isinstance(v, bytes):
        try:
            return v.decode("utf-8", errors="replace")
        except Exception:
            return repr(v)
    return str(v)


def _stream_csv_arrow(
    ctx: ReaderContext,
    progress: Optional[Callable[[int], None]] = None,
    progress_every_rows: int = 20000,
) -> Iterator[bytes]:
    """用 pyarrow.csv.CSVWriter 直写到内存 buffer，批量 yield。

    arrow 的 CSV writer 是 C 实现，escape 和 UTF-8 编码不走 Python 层。
    对大 JSON 字段场景，吞吐能提升 3-10×。
    """
    import pyarrow as pa
    from pyarrow import csv as pacsv

    yield UTF8_BOM

    processed = 0
    last_reported = 0
    writer: Optional[pacsv.CSVWriter] = None
    buf = pa.BufferOutputStream()

    # batch 级 prefetch：下载线程往前拉 batch，主线程专心写
    batches = prefetch(ctx.iter_arrow_batches(), bufsize=8)

    try:
        for batch in batches:
            if writer is None:
                write_options = pacsv.WriteOptions(include_header=True)
                writer = pacsv.CSVWriter(buf, batch.schema, write_options=write_options)
            writer.write_batch(batch)
            processed += batch.num_rows

            # 取出累积的字节并清空 buffer，实现流式输出
            chunk = buf.getvalue().to_pybytes()
            if chunk:
                yield chunk
            # BufferOutputStream 不支持 truncate，这里重开一个 + 重绑 writer
            buf = pa.BufferOutputStream()
            # writer 不能迁到新 buf：pyarrow CSVWriter 内部持有 stream 引用
            # 解决办法：每次重新创建 writer，但要关闭 header
            writer.close()
            writer = pacsv.CSVWriter(
                buf, batch.schema, write_options=pacsv.WriteOptions(include_header=False)
            )

            if progress and processed - last_reported >= progress_every_rows:
                progress(processed)
                last_reported = processed
    finally:
        if writer is not None:
            writer.close()
            tail = buf.getvalue().to_pybytes()
            if tail:
                yield tail

    if progress:
        progress(processed)


def _stream_csv_python(
    ctx: ReaderContext,
    chunk_rows: int = 5000,
    progress: Optional[Callable[[int], None]] = None,
) -> Iterator[bytes]:
    """Python csv 模块 + 行级 prefetch 的回退路径。"""
    yield UTF8_BOM

    buf = io.StringIO()
    writer = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\r\n")

    writer.writerow([c.name for c in ctx.columns])
    yield buf.getvalue().encode("utf-8")
    buf.seek(0)
    buf.truncate(0)

    processed = 0
    records = prefetch(ctx.iter_records(), bufsize=4000)
    for row in records:
        writer.writerow([_format_cell(v) for v in row])
        processed += 1
        if processed % chunk_rows == 0:
            yield buf.getvalue().encode("utf-8")
            buf.seek(0)
            buf.truncate(0)
            if progress:
                progress(processed)

    if buf.tell():
        yield buf.getvalue().encode("utf-8")
    if progress:
        progress(processed)


def stream_csv(
    ctx: ReaderContext,
    chunk_rows: int = 5000,
    progress: Optional[Callable[[int], None]] = None,
) -> Iterator[bytes]:
    """生成 UTF-8 BOM CSV 的字节流。arrow 可用时走 native 快速路径。"""
    if ctx.iter_arrow_batches is not None:
        try:
            yield from _stream_csv_arrow(ctx, progress=progress)
            return
        except Exception:
            # arrow 路径失败，回退到 Python 路径
            pass
    yield from _stream_csv_python(ctx, chunk_rows=chunk_rows, progress=progress)
