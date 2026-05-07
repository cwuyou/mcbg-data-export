"""xlsx 流式写入：constant_memory 模式，超过单 sheet 上限自动拆分。"""
from __future__ import annotations

import os
from typing import Any, Callable, Optional

import xlsxwriter

from .reader import ReaderContext, prefetch


XLSX_MAX_ROWS_PER_SHEET = 1048576
DATA_ROWS_PER_SHEET = XLSX_MAX_ROWS_PER_SHEET - 1
XLSX_MAX_CELL_CHARS = 32767
_TRUNC_MARK = "...[TRUNCATED]"


def _format_cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, bytes):
        try:
            s = v.decode("utf-8", errors="replace")
        except Exception:
            s = repr(v)
    elif isinstance(v, str):
        s = v
    else:
        s = str(v)
    if len(s) > XLSX_MAX_CELL_CHARS:
        return s[: XLSX_MAX_CELL_CHARS - len(_TRUNC_MARK)] + _TRUNC_MARK
    return s


def write_xlsx(
    ctx: ReaderContext,
    output_path: str,
    progress: Optional[Callable[[int], None]] = None,
    progress_every: int = 1000,
) -> int:
    """将 reader 内容写入 xlsx 文件，返回总行数。超过单 sheet 上限自动开新 sheet。"""
    tmpdir = os.path.dirname(os.path.abspath(output_path))

    workbook = xlsxwriter.Workbook(
        output_path,
        {
            "constant_memory": True,
            "strings_to_urls": False,
            "strings_to_numbers": False,
            "strings_to_formulas": False,
            "tmpdir": tmpdir,
        },
    )
    header_fmt = workbook.add_format({"bold": True})
    col_names = [c.name for c in ctx.columns]
    ncols = len(col_names)

    sheet_idx = 1
    worksheet = workbook.add_worksheet(f"Sheet{sheet_idx}")
    worksheet.write_row(0, 0, col_names, header_fmt)

    row_in_sheet = 1
    processed = 0
    # 读写并行：下载线程和 xlsx 写入线程重叠执行
    records = prefetch(ctx.iter_records(), bufsize=4000)
    try:
        for row in records:
            if row_in_sheet > DATA_ROWS_PER_SHEET:
                sheet_idx += 1
                worksheet = workbook.add_worksheet(f"Sheet{sheet_idx}")
                worksheet.write_row(0, 0, col_names, header_fmt)
                row_in_sheet = 1

            # 直接用底层 _write_string 绕过 write() 的类型判断，热点路径省 15-30%
            for col in range(ncols):
                worksheet._write_string(row_in_sheet, col, _format_cell(row[col]), None)

            row_in_sheet += 1
            processed += 1
            if progress and processed % progress_every == 0:
                progress(processed)
    finally:
        workbook.close()

    if progress:
        progress(processed)
    return processed
