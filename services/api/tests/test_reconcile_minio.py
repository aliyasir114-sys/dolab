"""The reconciliation job's ledger identity on MinIO (notice #57, decision ق3-ب): a read-only user
made in CI with mc and ops/ledger-reader-policy.json. It can list and read the ledger bucket, and
nothing else: no writing, no deleting, no photo bucket."""
import os
import uuid

import pytest

from app import ledger
from app.jobs import reconcile as R
from tests.test_reconcile_job import KEYS, FakeDb

READER_KEY = os.getenv("DOLAB_TEST_LEDGER_READER_KEY", "")
pytestmark = pytest.mark.skipif(not READER_KEY, reason="needs MinIO with the read-only ledger user (CI)")

BUCKET = os.getenv("DOLAB_LEDGER_BUCKET", "dolab-ci-ledger")
PHOTOS = os.getenv("DOLAB_S3_BUCKET", "dolab-ci")
ENDPOINT = os.getenv("DOLAB_S3_ENDPOINT_URL", "http://localhost:9000")


def client(key=None, secret=None):
    import boto3.session

    return boto3.session.Session(aws_access_key_id=key, aws_secret_access_key=secret).client(
        "s3", endpoint_url=ENDPOINT, region_name="us-east-1")


@pytest.fixture
def reader():
    return client(READER_KEY, os.environ["DOLAB_TEST_LEDGER_READER_SECRET"])


def denied(call) -> bool:
    from botocore.exceptions import ClientError

    try:
        call()
    except ClientError as error:
        return error.response["Error"]["Code"] == "AccessDenied"
    return False


def test_the_job_reads_the_ledger_bucket_with_the_read_only_identity(reader):
    from datetime import datetime, timezone

    admin = client()
    signer = ledger.private_key_from_seed(ledger.TEST_SEEDS["test-1"])
    ours = [ledger.make_entry(signer, "test-1", "item", str(uuid.uuid4()), str(uuid.uuid4()),
                              datetime.now(timezone.utc), environment="test") for _ in range(3)]
    for e in ours:
        admin.put_object(Bucket=BUCKET, Key=e.object_name, Body=e.to_json())
    db = FakeDb()
    report = R.run(R.S3LedgerReader(BUCKET, reader), db, KEYS)
    assert not report.listing_failed and report.errors == 0
    assert {e.entry_id for e in ours} <= {c[1] for c in db.calls if c[0] == "reconcile"}


def test_the_read_only_identity_cannot_write_delete_or_see_photos(reader):
    admin = client()
    name = f"2026/01/01/{uuid.uuid4()}.json"
    admin.put_object(Bucket=BUCKET, Key=name, Body=b"{}")
    assert denied(lambda: reader.put_object(Bucket=BUCKET, Key=f"2026/01/01/{uuid.uuid4()}.json", Body=b"{}"))
    assert denied(lambda: reader.put_object(Bucket=BUCKET, Key=name, Body=b"replaced"))
    assert denied(lambda: reader.delete_object(Bucket=BUCKET, Key=name))
    # A batch delete answers per key (MinIO: 200 with an error entry) rather than failing outright.
    try:
        answer = reader.delete_objects(Bucket=BUCKET, Delete={"Objects": [{"Key": name}]})
        assert not answer.get("Deleted") and answer["Errors"][0]["Code"] == "AccessDenied"
    except Exception as error:  # noqa: BLE001
        assert getattr(error, "response", {}).get("Error", {}).get("Code") == "AccessDenied"
    assert admin.get_object(Bucket=BUCKET, Key=name)["Body"].read() == b"{}"  # still there, unchanged
    assert denied(lambda: reader.list_objects_v2(Bucket=PHOTOS))
    assert denied(lambda: reader.put_object(Bucket=PHOTOS, Key="x", Body=b"x"))
    assert denied(lambda: reader.delete_bucket(Bucket=BUCKET))
