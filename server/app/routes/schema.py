from fastapi import APIRouter, Depends, HTTPException

from ..models import (
    SchemaRequest,
    SchemaResponse,
    ColumnInfo,
    PartitionsRequest,
    PartitionsResponse,
)
from ..services.odps_client import credentials_from_headers, build_odps, OdpsCredentials

router = APIRouter()


@router.post("/api/schema", response_model=SchemaResponse)
def get_schema(
    req: SchemaRequest,
    creds: OdpsCredentials = Depends(credentials_from_headers),
):
    odps = build_odps(creds)
    try:
        table = odps.get_table(req.table)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"table not found: {e}")

    schema = table.table_schema
    data_cols = [
        ColumnInfo(name=c.name, type=str(c.type), comment=c.comment)
        for c in schema.simple_columns
    ]
    partition_cols = [
        ColumnInfo(name=c.name, type=str(c.type), comment=c.comment)
        for c in schema.partitions or []
    ]

    row_count = None
    size_bytes = None
    try:
        if req.partition and partition_cols:
            p = table.get_partition(req.partition)
            row_count = p.record_num if p.record_num is not None and p.record_num >= 0 else None
            size_bytes = p.size
        elif not partition_cols:
            row_count = table.record_num if table.record_num is not None and table.record_num >= 0 else None
            size_bytes = table.size
    except Exception:
        pass

    return SchemaResponse(
        table=req.table,
        columns=data_cols,
        is_partitioned=bool(partition_cols),
        partition_columns=partition_cols,
        row_count=row_count,
        size_bytes=size_bytes,
    )


@router.post("/api/partitions", response_model=PartitionsResponse)
def list_partitions(
    req: PartitionsRequest,
    creds: OdpsCredentials = Depends(credentials_from_headers),
):
    odps = build_odps(creds)
    try:
        table = odps.get_table(req.table)
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"table not found: {e}")

    if not table.table_schema.partitions:
        return PartitionsResponse(partitions=[])

    try:
        parts = [str(p.partition_spec) for p in table.iterate_partitions()]
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"list partitions failed: {e}")
    # 按字符串倒序返回。分区命名通常是 dt=YYYYMMDD / dt='YYYY-MM-DD'，
    # 字符串逆序即等于日期倒序。
    parts.sort(reverse=True)
    return PartitionsResponse(partitions=parts)
