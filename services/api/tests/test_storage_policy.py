"""The file deletion job's storage policy is rendered per environment (review of #60): one
action, DeleteObject, on the objects of that environment's photo bucket, and nothing else."""
import importlib.util
import json
from pathlib import Path

import pytest

_PATH = Path(__file__).resolve().parent.parent / "ops" / "render_policy.py"
_spec = importlib.util.spec_from_file_location("render_policy", _PATH)
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)


@pytest.mark.parametrize("bucket", ["dolab-ci", "dolab-photos-staging", "dolab.photos.prod", "abc"])
def test_it_allows_delete_on_that_bucket_only(bucket):
    policy = P.render(bucket, "dolab-ledger")
    assert policy["Statement"] == [{"Sid": "DeletePhotoFilesOnly", "Effect": "Allow",
                                    "Action": ["s3:DeleteObject"], "Resource": [f"arn:aws:s3:::{bucket}/*"]}]
    text = json.dumps(policy)
    assert "*\"" not in text.replace(f"{bucket}/*\"", "")  # no other wildcard anywhere
    for action in ("GetObject", "PutObject", "ListBucket", "DeleteBucket", "s3:*"):
        assert action not in text


@pytest.mark.parametrize("bucket", ["", "*", "dolab-*", "Dolab-CI", "dolab-ci/photos", "dolab-ci/*", "ab",
                                    "-dolab", "dolab-", "dolab..ci", "dolab.-ci", "192.168.1.1", "a" * 64,
                                    "dolab ci", "arn:aws:s3:::x"])
def test_it_refuses_anything_but_a_plain_bucket_name(bucket):
    with pytest.raises(P.PolicyError):
        P.render(bucket)


def test_it_refuses_the_ledger_bucket():
    with pytest.raises(P.PolicyError, match="ledger"):
        P.render("dolab-ledger", "dolab-ledger")


def test_the_command_line(capsys):
    assert P.main(["--photo-bucket", "dolab-photos-prod", "--ledger-bucket", "dolab-ledger-prod"]) == 0
    assert json.loads(capsys.readouterr().out)["Statement"][0]["Resource"] == ["arn:aws:s3:::dolab-photos-prod/*"]
    assert P.main(["--photo-bucket", "*"]) == 2
    assert "refused" in capsys.readouterr().err


def test_a_widened_template_is_refused(tmp_path, monkeypatch):
    wide = json.loads(P.TEMPLATE.read_text())
    wide["Statement"][0]["Action"].append("s3:GetObject")
    template = tmp_path / "t.json"
    template.write_text(json.dumps(wide))
    monkeypatch.setattr(P, "TEMPLATE", template)
    with pytest.raises(P.PolicyError, match="more than DeleteObject"):
        P.render("dolab-ci")
