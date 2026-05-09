"""统一的读取层：封装 Tunnel download 和 SQL instance 两种数据源。

提供两种接口：
- iter_records / iter_records_pyarrow：按行 yield Python list，适合 xlsx 写入
- iter_arrow_batches：yield pyarrow.RecordBatch，供 CSV 快速路径走 pyarrow.csv 原生写
"""
from __future__ import annotations

import os
import queue
import threading
from dataclasses import dataclass
from typing import Iterator, List, Optional, Any, Callable

from odps import ODPS


@dataclass
class ColumnMeta:
    name: str
    type: str


@dataclass
class ReaderContext:
    columns: List[ColumnMeta]
    total: Optional[int]
    iter_records: Callable[[], Iterator[List[Any]]]
    # 可选：返回 pyarrow.RecordBatch 的迭代器；None 表示该源不支持 arrow
    iter_arrow_batches: Optional[Callable[[], Iterator[Any]]] = None
    # 数据源在 MaxCompute 里的存储字节数（列存压缩后的大小）。
    # None 表示源不支持（如 SQL Instance），调用方需要降级到只用行数判断。
    size_bytes: Optional[int] = None


_SENTINEL = object()


def prefetch(it: Iterator[Any], bufsize: int = 200) -> Iterator[Any]:
    """把迭代器放到后台线程里跑，主线程从队列取。读写并行。

    对于 batch 级迭代器 bufsize 用较小值（10~200），避免缓存过多大对象。
    对于行级迭代器可以用更大的 bufsize。
    """
    q: queue.Queue = queue.Queue(maxsize=bufsize)
    err_holder: List[BaseException] = []

    def producer():
        try:
            for item in it:
                q.put(item)
        except BaseException as e:
            err_holder.append(e)
        finally:
            q.put(_SENTINEL)

    t = threading.Thread(target=producer, name="prefetch", daemon=True)
    t.start()

    while True:
        item = q.get()
        if item is _SENTINEL:
            break
        yield item

    if err_holder:
        raise err_holder[0]


def _record_to_list(record: Any, column_names: List[str]) -> List[Any]:
    return [record[name] for name in column_names]


def _iter_arrow_batch_rows(batches_iter: Iterator[Any], column_names: List[str]) -> Iterator[List[Any]]:
    """从 arrow batches 按行 yield Python list。供 xlsx 写入使用。"""
    for batch in batches_iter:
        cols = [batch.column(name).to_pylist() for name in column_names]
        n = batch.num_rows
        for i in range(n):
            yield [c[i] for c in cols]


def open_table_reader(
    odps: ODPS,
    table: str,
    partition: Optional[str],
    columns: Optional[List[str]],
) -> ReaderContext:
    """Tunnel 单 session 读取。对大 JSON 场景，CSV 路径会走 arrow-native。"""
    from odps.tunnel import TableTunnel

    t = odps.get_table(table)
    all_cols = [ColumnMeta(c.name, str(c.type)) for c in t.table_schema.simple_columns]
    use_cols = columns or [c.name for c in all_cols]
    col_set = set(use_cols)
    out_cols = [c for c in all_cols if c.name in col_set]

    tunnel = TableTunnel(odps)
    session = tunnel.create_download_session(
        table,
        partition_spec=partition if partition else None,
    )
    total = session.count

    # 抓表 / 分区的存储字节数。list 列存 + 压缩后的大小，用于预估 CSV 体积。
    # pyodps 某些版本属性不同，全部 try/except 兜底。
    size_bytes: Optional[int] = None
    try:
        if partition:
            p = t.get_partition(partition)
            size_bytes = getattr(p, "size", None)
        else:
            size_bytes = getattr(t, "size", None)
        # 字段子集时按比例缩放（粗估）
        if size_bytes and columns and all_cols:
            size_bytes = int(size_bytes * len(use_cols) / len(all_cols))
    except Exception:
        size_bytes = None

    def iter_arrow_batches() -> Iterator[Any]:
        try:
            arrow_reader = session.open_arrow_reader(0, total, columns=use_cols)
        except (AttributeError, Exception):
            raise
        with arrow_reader as reader:
            for batch in reader:
                yield batch

    def iter_records() -> Iterator[List[Any]]:
        try:
            it = iter_arrow_batches()
            yield from _iter_arrow_batch_rows(it, use_cols)
            return
        except Exception:
            pass
        with session.open_record_reader(0, total, columns=use_cols) as reader:
            for rec in reader:
                yield _record_to_list(rec, use_cols)

    # 检查 arrow 可用性；不可用时 iter_arrow_batches 置 None，调用方走 fallback
    arrow_supported = hasattr(session, "open_arrow_reader")

    return ReaderContext(
        columns=out_cols,
        total=total,
        iter_records=iter_records,
        iter_arrow_batches=iter_arrow_batches if arrow_supported else None,
        size_bytes=size_bytes,
    )


def open_sql_reader(odps: ODPS, sql: str) -> ReaderContext:
    """执行 SQL 并通过 Instance Tunnel 读取结果。"""
    instance = odps.execute_sql(sql)

    try:
        arrow_reader = instance.open_reader(tunnel=True, arrow=True)
    except Exception:
        arrow_reader = None

    if arrow_reader is not None:
        try:
            total = arrow_reader.count
        except Exception:
            total = None
        arrow_schema = arrow_reader.schema
        out_cols = [ColumnMeta(c.name, str(c.type)) for c in arrow_schema.simple_columns]
        col_names = [c.name for c in out_cols]

        # 标记是否已经开始从 arrow_reader 消费 batch。一旦开始就不能再切非-arrow
        # reader（游标已经前进，也不保证还能再次 open_reader）。只在"还没吐过数据"
        # 的启动期允许回退到非-arrow 路径（主要覆盖 Windows 缺 tzdata 的场景）。
        arrow_started = {"v": False}

        def iter_arrow_batches() -> Iterator[Any]:
            for batch in arrow_reader:
                arrow_started["v"] = True
                yield batch

        def iter_records() -> Iterator[List[Any]]:
            try:
                yield from _iter_arrow_batch_rows(iter_arrow_batches(), col_names)
                return
            except Exception:
                if arrow_started["v"]:
                    raise
            # 启动期失败（tzdata / schema 转换等），退回非-arrow reader
            fallback = instance.open_reader(tunnel=True)
            for rec in fallback:
                yield _record_to_list(rec, col_names)

        return ReaderContext(
            columns=out_cols,
            total=total,
            iter_records=iter_records,
            iter_arrow_batches=iter_arrow_batches,
        )

    reader = instance.open_reader(tunnel=True)
    try:
        total = reader.count
    except Exception:
        total = None
    schema = reader.schema
    out_cols = [ColumnMeta(c.name, str(c.type)) for c in schema.simple_columns]
    col_names = [c.name for c in out_cols]

    def iter_records() -> Iterator[List[Any]]:
        for rec in reader:
            yield _record_to_list(rec, col_names)

    return ReaderContext(
        columns=out_cols,
        total=total,
        iter_records=iter_records,
        iter_arrow_batches=None,
    )
