from __future__ import annotations

from dataclasses import dataclass
from fastapi import Header, HTTPException
from odps import ODPS


@dataclass(frozen=True)
class OdpsCredentials:
    access_id: str
    access_key: str
    endpoint: str
    project: str


def credentials_from_headers(
    x_odps_access_id: str = Header(..., alias="X-ODPS-Access-Id"),
    x_odps_access_key: str = Header(..., alias="X-ODPS-Access-Key"),
    x_odps_endpoint: str = Header(..., alias="X-ODPS-Endpoint"),
    x_odps_project: str = Header(..., alias="X-ODPS-Project"),
) -> OdpsCredentials:
    if not all([x_odps_access_id, x_odps_access_key, x_odps_endpoint, x_odps_project]):
        raise HTTPException(status_code=400, detail="missing ODPS credentials in headers")
    return OdpsCredentials(
        access_id=x_odps_access_id,
        access_key=x_odps_access_key,
        endpoint=x_odps_endpoint,
        project=x_odps_project,
    )


def build_odps(creds: OdpsCredentials) -> ODPS:
    return ODPS(
        access_id=creds.access_id,
        secret_access_key=creds.access_key,
        project=creds.project,
        endpoint=creds.endpoint,
    )
