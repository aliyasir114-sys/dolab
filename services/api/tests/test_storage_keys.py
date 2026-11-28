"""One photo-key check for every photo-file deletion (review of #60, High 1 and Medium 3): exact,
anchored at both ends, so no other path, no trailing newline and no control character passes."""
import pytest

from app.storage_keys import check_key, is_photo_key

USER = "6f1c0d2e-0000-4000-8000-000000000001"
PHOTO = "6f1c0d2e-0000-4000-8000-0000000000aa"
GOOD = [f"{USER}/{PHOTO}_original.jpg", f"{USER}/{PHOTO}_original.png", f"{USER}/{PHOTO}_original.webp",
        f"{USER}/{PHOTO}_thumb.jpg"]


@pytest.mark.parametrize("key", GOOD)
def test_photo_keys_as_the_server_makes_them(key):
    assert is_photo_key(key) and check_key(key) == key


@pytest.mark.parametrize("suffix", ["\n", "\r", "\r\n", "\x00", "\t", " ", "\x0b", "\x0c", "\x1b", " ", "\x85"])
def test_a_newline_or_control_character_at_the_end_is_refused(suffix):
    for key in GOOD:
        assert not is_photo_key(key + suffix)
        with pytest.raises(ValueError):
            check_key(key + suffix)


@pytest.mark.parametrize("key", [
    "u/x.jpg", "x.jpg", "", "/" + GOOD[0], GOOD[0] + "/", "\n" + GOOD[0], GOOD[0].replace("/", "\n"),
    GOOD[0].upper(), GOOD[0].replace("_original", "_original_"), GOOD[0] + ".jpg", GOOD[0][:-1],
    f"{USER}/../{PHOTO}_original.jpg", f"{USER}/{PHOTO}_original.exe", f"{USER}/{PHOTO}_thumb.png",
    f"2026/11/28/{PHOTO}.json", f"{USER}/{PHOTO}_original.jpg\n{USER}/{PHOTO}_thumb.jpg",
    GOOD[0].replace("-", "‐"),  # a look-alike hyphen
])
def test_anything_else_is_not_a_photo_key(key):
    assert not is_photo_key(key)


def test_non_strings_are_refused():
    for value in (None, 1, b"x", ["x"]):
        assert not is_photo_key(value)
        with pytest.raises(ValueError):
            check_key(value)
