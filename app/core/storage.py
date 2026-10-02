"""S3 호환 파일 저장소 클라이언트 (로컬: SeaweedFS, 배포: AWS S3).

파일은 서버끼리 주고받지 않고 key(경로)만 넘긴다.
브라우저는 presigned URL로 직접 업로드·다운로드한다.
key 규칙(초안): {workspace_id}/{song_id}/...
"""

from functools import lru_cache
from typing import TYPE_CHECKING

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


def presigned_upload_url(key: str, content_type: str) -> str:
    s = get_settings()
    return _get_public_s3().generate_presigned_url(
        "put_object",
        Params={"Bucket": s.s3_bucket, "Key": key, "ContentType": content_type},
        ExpiresIn=s.s3_presigned_expires_seconds,
    )


def presigned_download_url(key: str) -> str:
    s = get_settings()
    return _get_public_s3().generate_presigned_url(
        "get_object",
        Params={"Bucket": s.s3_bucket, "Key": key},
        ExpiresIn=s.s3_presigned_expires_seconds,
    )
