"""CSV 流式写入：
- 优先走 pyarrow 原生 CSV writer（C 扩展，释放 GIL，对长字符串字段吞吐远高于 Python csv 模块）
- 不可用时回退到 Python csv + 行级迭代
- 支持对大文件按需 gzip 压缩输出
"""
from __future__ import annotations

import csv
import gzip
import io
from typing import Iterator, List, Any, Callable, Optional

from .reader import ReaderContext, prefetch


UTF8_BOM = b"\xef\xbb\xbf"

# 压缩决策阈值。两个条件任一满足就开启 gzip：
# 1. 行数：宽表少行也可能很大，但行数超过一定规模基本都该压缩
# 2. 预估 CSV 未压缩字节：用 MaxCompute 存储大小 × 膨胀系数
#    实测 IM 大 JSON 表：9.44G 列存 → 30G CSV ≈ 3.2x；普通表膨胀 2~3x
#    取中间值 3.0 作为膨胀系数
GZIP_ROW_THRESHOLD = 500_000
GZIP_CSV_BYTES_THRESHOLD = 500 * 1024 * 1024   # 500 MB
CSV_INFLATION_FACTOR = 3.0


def _estimate_csv_bytes(ctx: ReaderContext) -> Optional[int]:
    """根据 MaxCompute 存储大小估算 CSV 未压缩体积。拿不到返回 None。"""
    if ctx.size_bytes is None or ctx.size_bytes <= 0:
        return None
    return int(ctx.size_bytes * CSV_INFLATION_FACTOR)


def should_gzip(ctx: ReaderContext) -> bool:
    """决定是否对该次 CSV 导出开启 gzip 压缩。

    双阈值"或"逻辑 —— 行数超限或估算体积超限都压缩。
    - 表模式（无 where）：两个信号都有，判断最准
    - SQL 模式 / 带 where：size_bytes 为 None，退化到只看行数
    - 都为 None（如某些 SQL 拿不到 count）：保守不压缩
    """
    if ctx.total is not None and ctx.total >= GZIP_ROW_THRESHOLD:
        return True
    est_bytes = _estimate_csv_bytes(ctx)
    if est_bytes is not None and est_bytes >= GZIP_CSV_BYTES_THRESHOLD:
        return True
    return False


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

    注意：BOM 在第一个 batch 成功写入后才 yield，保证启动期失败（如 Windows 缺
    tzdata 无法处理带时区 TIMESTAMP）时上层能干净回退到 Python 路径。
    """
    import pyarrow as pa
    from pyarrow import csv as pacsv

    processed = 0
    last_reported = 0
    writer: Optional[pacsv.CSVWriter] = None
    buf = pa.BufferOutputStream()
    header_yielded = False

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
                if not header_yielded:
                    yield UTF8_BOM
                    header_yielded = True
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
                if not header_yielded:
                    yield UTF8_BOM
                    header_yielded = True
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


def _can_use_arrow_path(ctx: ReaderContext) -> bool:
    """预检 arrow 路径是否可用。比如 Windows 下带时区的 TIMESTAMP 列需要 tzdata，
    没装就提前回退，避免写到一半崩。"""
    if ctx.iter_arrow_batches is None:
        return False
    try:
        import pyarrow as pa  # noqa: F401
    except Exception:
        return False
    return True


def stream_csv(
    ctx: ReaderContext,
    chunk_rows: int = 5000,
    progress: Optional[Callable[[int], None]] = None,
) -> Iterator[bytes]:
    """生成 UTF-8 BOM CSV 的字节流。arrow 可用时走 native 快速路径。

    arrow 路径只在"还没 yield 任何数据"之前才能安全回退，一旦开始输出就不能再换
    路径（header 已经写过了）。这里用 `started` 标志严格区分两个阶段。
    """
    if _can_use_arrow_path(ctx):
        started = False

        def _arrow_gen():
            nonlocal started
            for chunk in _stream_csv_arrow(ctx, progress=progress):
                started = True
                yield chunk

        try:
            yield from _arrow_gen()
            return
        except Exception:
            if started:
                # 已经输出了 BOM / header / 部分 batch，回退会破坏文件结构，必须抛
                raise
            # arrow 路径启动期失败（schema/tzdata/reader 构造问题），静默回退
    yield from _stream_csv_python(ctx, chunk_rows=chunk_rows, progress=progress)


def gzip_wrap(chunks: Iterator[bytes], compresslevel: int = 5) -> Iterator[bytes]:
    """把一个字节流包装为 gzip 压缩流。

    compresslevel=5：速度 / 压缩比折中。CSV 文本压缩率很高，6 和 5 差距很小，
    5 对 CPU 更友好，单核 ECS 不至于打满。
    """
    buf = io.BytesIO()
    gz = gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=compresslevel)
    try:
        for chunk in chunks:
            gz.write(chunk)
            # 把累积的压缩字节 yield 出去
            data = buf.getvalue()
            if data:
                yield data
                buf.seek(0)
                buf.truncate(0)
    finally:
        gz.close()
        tail = buf.getvalue()
        if tail:
            yield tail


def stream_csv_maybe_gzip(
    ctx: ReaderContext,
    chunk_rows: int = 5000,
    progress: Optional[Callable[[int], None]] = None,
) -> tuple[Iterator[bytes], bool]:
    """入口函数：返回 (字节流迭代器, 是否 gzip 压缩)。

    调用方据 is_gzip 决定文件扩展名 (.csv / .csv.gz) 和 Content-Type。
    """
    raw = stream_csv(ctx, chunk_rows=chunk_rows, progress=progress)
    if should_gzip(ctx):
        return gzip_wrap(raw), True
    return raw, False
