"""S3 호환 파일 저장소 클라이언트 (로컬: SeaweedFS, 배포: AWS S3).

파일은 서버로 주고받지 않고 key(경로)만 넘긴다.
브라우저는 presigned URL로 직접 업로드·다운로드한다.
key 규칙(초안): {workspace_id}/{song_id}/...
"""

from functools import lru_cache
from typing import TYPE_CHECKING
from urllib.parse import quote

import boto3
from botocore.client import Config
from botocore.exceptions import ClientError

from app.core.config import get_settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client


def _client(endpoint_url: str) -> "S3Client":
    s = get_settings()
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        aws_access_key_id=s.s3_access_key,
        aws_secret_access_key=s.s3_secret_key,
        region_name=s.s3_region,
        config=Config(signature_version="s3v4", s3={"addressing_style": "path"}),
    )


@lru_cache
def get_s3() -> "S3Client":
    """서버 내부 통신용 클라이언트 (예: http://seaweedfs:8333)."""
    return _client(get_settings().s3_endpoint_url)


@lru_cache
def _get_public_s3() -> "S3Client":
    """presigned URL 서명용 클라이언트. 브라우저가 접근 가능한 주소로 서명해야 한다."""
    return _client(get_settings().s3_public_endpoint_url)


def ensure_bucket() -> None:
    s3 = get_s3()
    bucket = get_settings().s3_bucket
    try:
        s3.head_bucket(Bucket=bucket)
    except ClientError:
        s3.create_bucket(Bucket=bucket)


def download_file(key: str, path: str) -> None:
    """저장소의 key를 로컬 파일로 내려받는다 (worker 전용)."""
    get_s3().download_file(get_settings().s3_bucket, key, path)


def upload_file(path: str, key: str, content_type: str) -> None:
    """로컬 파일을 저장소 key로 올린다 (worker 전용)."""
    get_s3().upload_file(
        path, get_settings().s3_bucket, key, ExtraArgs={"ContentType": content_type}
    )


def read_bytes(key: str) -> bytes:
    """저장소 key의 내용을 읽는다 (작은 JSON 결과 조회용)."""
    obj = get_s3().get_object(Bucket=get_settings().s3_bucket, Key=key)
    return bytes(obj["Body"].read())


def put_bytes(key: str, data: bytes, content_type: str) -> None:
    """작은 데이터(JSON 등)를 저장소 key로 올린다."""
    get_s3().put_object(
        Bucket=get_settings().s3_bucket, Key=key, Body=data, ContentType=content_type
    )


def presigned_upload_url(key: str, content_type: str) -> str:
    s = get_settings()
    return _get_public_s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": s.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=s.s3_presigned_expires_seconds,
    )


def presigned_download_url(
    key: str,
    download_name: str | None = None,
    *,
    expires_in_seconds: int | None = None,
) -> str:
    """download_name을 주면 브라우저가 그 이름으로 내려받는다 (attachment)."""
    s = get_settings()
    params: dict[str, str] = {"Bucket": s.s3_bucket, "Key": key}
    if download_name is not None:
        params["ResponseContentDisposition"] = (
            f"attachment; filename*=UTF-8''{quote(download_name)}"
        )
    expires = expires_in_seconds or s.s3_presigned_expires_seconds
    return _get_public_s3().generate_presigned_url("get_object", Params=params, ExpiresIn=expires)


def file_exists(key: str) -> bool:
    """저장소에 key가 실제로 존재하는지 확인한다."""
    try:
        get_s3().head_object(Bucket=get_settings().s3_bucket, Key=key)
        return True
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


def object_size(key: str) -> int:
    """저장소 객체의 바이트 크기를 반환한다. 존재하지 않으면 0."""
    try:
        head = get_s3().head_object(Bucket=get_settings().s3_bucket, Key=key)
        return int(head.get("ContentLength", 0))
    except ClientError as exc:
        code = str(exc.response.get("Error", {}).get("Code", ""))
        if code in ("404", "NoSuchKey", "NotFound"):
            return 0
        raise
