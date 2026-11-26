"""The file deletion job's storage identity on MinIO (notice #59, decision ط2): a user made in CI
with mc and ops/file-cleaner-policy.json. It can delete objects in the photo bucket, and nothing
else: no reading, listing or writing there, nothing in the ledger bucket."""
import os
import uuid

import pytest

from app.jobs import file_deletions as J

CLEANER_KEY = os.getenv("DOLAB_TEST_FILE_CLEANER_KEY", "")
pytestmark = pytest.mark.skipif(not CLEANER_KEY, reason="needs MinIO with the file cleaner user (CI)")

PHOTOS = os.getenv("DOLAB_S3_BUCKET", "dolab-ci")
LEDGER = os.getenv("DOLAB_LEDGER_BUCKET", "dolab-ci-ledger")
ENDPOINT = os.getenv("DOLAB_S3_ENDPOINT_URL", "http://localhost:9000")


def client(key=None, secret=None):
    import boto3.session

    return boto3.session.Session(aws_access_key_id=key, aws_secret_access_key=secret).client(
        "s3", endpoint_url=ENDPOINT, region_name="us-east-1")


@pytest.fixture
def cleaner():
    return client(CLEANER_KEY, os.environ["DOLAB_TEST_FILE_CLEANER_SECRET"])


def denied(call) -> bool:
    from botocore.exceptions import ClientError

    try:
        call()
    except ClientError as error:
        return error.response["Error"]["Code"] == "AccessDenied"
    return False


def exists(admin, bucket, key) -> bool:
    from botocore.exceptions import ClientError

    try:
        admin.head_object(Bucket=bucket, Key=key)
        return True
    except ClientError:
        return False


def test_the_job_deletes_photo_files_with_its_own_identity(cleaner):
    admin = client()
    key = f"{uuid.uuid4()}/{uuid.uuid4()}_original.jpg"
    admin.put_object(Bucket=PHOTOS, Key=key, Body=b"jpeg")
    deleter = J.S3FileDeleter(PHOTOS, cleaner)
    deleter.delete(key)
    assert not exists(admin, PHOTOS, key)
    deleter.delete(key)  # already gone: not an error


def test_the_file_cleaner_can_do_nothing_else(cleaner):
    admin = client()
    photo = f"{uuid.uuid4()}/{uuid.uuid4()}_thumb.jpg"
    entry = f"2026/01/02/{uuid.uuid4()}.json"
    admin.put_object(Bucket=PHOTOS, Key=photo, Body=b"jpeg")
    admin.put_object(Bucket=LEDGER, Key=entry, Body=b"{}")
    assert denied(lambda: cleaner.get_object(Bucket=PHOTOS, Key=photo))
    assert denied(lambda: cleaner.list_objects_v2(Bucket=PHOTOS))
    assert denied(lambda: cleaner.put_object(Bucket=PHOTOS, Key=photo, Body=b"replaced"))
    assert denied(lambda: cleaner.get_object(Bucket=LEDGER, Key=entry))
    assert denied(lambda: cleaner.delete_object(Bucket=LEDGER, Key=entry))
    assert denied(lambda: cleaner.list_objects_v2(Bucket=LEDGER))
    assert denied(lambda: cleaner.delete_bucket(Bucket=PHOTOS))
    assert exists(admin, LEDGER, entry)
    assert admin.get_object(Bucket=PHOTOS, Key=photo)["Body"].read() == b"jpeg"
