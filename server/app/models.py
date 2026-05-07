from typing import Optional, List, Literal
from pydantic import BaseModel, Field


class OdpsAuth(BaseModel):
    access_id: str
    access_key: str
    endpoint: str
    project: str


class SchemaRequest(BaseModel):
    table: str
    partition: Optional[str] = None


class ColumnInfo(BaseModel):
    name: str
    type: str
    comment: Optional[str] = None


class SchemaResponse(BaseModel):
    table: str
    columns: List[ColumnInfo]
    is_partitioned: bool
    partition_columns: List[ColumnInfo] = Field(default_factory=list)
    row_count: Optional[int] = None
    size_bytes: Optional[int] = None


class PartitionsRequest(BaseModel):
    table: str


class PartitionsResponse(BaseModel):
    partitions: List[str]


class ExportRequest(BaseModel):
    table: Optional[str] = None
    partition: Optional[str] = None
    columns: Optional[List[str]] = None
    where: Optional[str] = None
    sql: Optional[str] = None
    format: Literal["xlsx", "csv"] = "xlsx"
    filename: Optional[str] = None


class ExportTaskResponse(BaseModel):
    task_id: str


class TaskStatus(BaseModel):
    task_id: str
    status: Literal["pending", "running", "done", "failed", "cancelled"]
    processed: int = 0
    total: Optional[int] = None
    message: Optional[str] = None
    download_url: Optional[str] = None
    filename: Optional[str] = None
