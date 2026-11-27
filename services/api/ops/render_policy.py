"""Render the file deletion job's storage policy for one environment (notice #59, review of #60).

    python ops/render_policy.py --photo-bucket <bucket> [--ledger-bucket <bucket>] > policy.json

The template (ops/file-cleaner-policy.template.json) allows one action, s3:DeleteObject, on the
objects of one bucket: the environment's photo bucket, the same value as that environment's
DOLAB_S3_BUCKET. Nothing is added. The bucket name is checked first, so a policy never covers
more than that bucket: no wildcard, no other bucket, no path, and never the ledger bucket.

Per environment (CI, staging, production): render with that environment's DOLAB_S3_BUCKET and
DOLAB_LEDGER_BUCKET, attach the output to the identity whose credentials the job alone receives,
and give that identity nothing else.
"""
import argparse
import json
import re
import sys
from pathlib import Path

TEMPLATE = Path(__file__).with_name("file-cleaner-policy.template.json")
PLACEHOLDER = "${PHOTO_BUCKET}"
# S3 bucket naming rules: 3-63 characters, lower-case letters, digits, dots and hyphens, starting
# and ending with a letter or digit, no "..", not shaped like an IP address.
_BUCKET = re.compile(r"^[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]$")
_IP = re.compile(r"^\d{1,3}(\.\d{1,3}){3}$")


class PolicyError(ValueError):
    pass


def check_bucket(name: str) -> str:
    if not _BUCKET.match(name or "") or ".." in name or ".-" in name or "-." in name or _IP.match(name):
        raise PolicyError(f"not a valid S3 bucket name: {name!r}")
    return name


def render(photo_bucket: str, ledger_bucket: str | None = None) -> dict:
    check_bucket(photo_bucket)
    if ledger_bucket and photo_bucket == ledger_bucket:
        raise PolicyError("the photo bucket must not be the ledger bucket")
    text = TEMPLATE.read_text(encoding="utf-8")
    if text.count(PLACEHOLDER) != 1:
        raise PolicyError("the template must name the photo bucket exactly once")
    policy = json.loads(text.replace(PLACEHOLDER, photo_bucket))
    # The rendered policy may hold exactly one statement: DeleteObject on this bucket's objects.
    expected = [{"Sid": "DeletePhotoFilesOnly", "Effect": "Allow", "Action": ["s3:DeleteObject"],
                 "Resource": [f"arn:aws:s3:::{photo_bucket}/*"]}]
    if policy.get("Statement") != expected:
        raise PolicyError("the template allows more than DeleteObject on the photo bucket")
    return policy


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--photo-bucket", required=True)
    parser.add_argument("--ledger-bucket")
    args = parser.parse_args(argv)
    try:
        policy = render(args.photo_bucket, args.ledger_bucket)
    except PolicyError as error:
        print(f"refused: {error}", file=sys.stderr)
        return 2
    json.dump(policy, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
