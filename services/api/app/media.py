"""Photo checks, clean-up and signed links."""
import hashlib
import hmac
import io
import time
from dataclasses import dataclass

from PIL import Image, ImageOps, UnidentifiedImageError

from .config import settings

# Decided from the file's real content, never from its name or declared type.
_FORMATS = {"JPEG": ("image/jpeg", "jpg"), "PNG": ("image/png", "png"), "WEBP": ("image/webp", "webp")}
MAX_PIXELS = 40_000_000
THUMB_SIZE = 480
VARIANTS = ("original", "thumb")


class InvalidImage(ValueError):
    pass


@dataclass(frozen=True)
class ProcessedImage:
    original: bytes
    thumb: bytes
    content_type: str
    extension: str
    width: int
    height: int


def process_image(data: bytes) -> ProcessedImage:
    """Validate an upload and return a cleaned original plus a thumbnail.

    The image is re-encoded, which drops EXIF and every other embedded block, so location
    and device details in a phone photo are not stored.
    """
    try:
        with Image.open(io.BytesIO(data)) as probe:
            image_format = probe.format
            if image_format not in _FORMATS:
                raise InvalidImage("only JPEG, PNG and WebP photos are accepted")
            if probe.width * probe.height > MAX_PIXELS:
                raise InvalidImage("image dimensions are too large")
            probe.verify()
        with Image.open(io.BytesIO(data)) as opened:
            image = ImageOps.exif_transpose(opened)
            image.load()
    except InvalidImage:
        raise
    except (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError) as error:
        raise InvalidImage("file is not a readable image") from error

    content_type, extension = _FORMATS[image_format]
    if image_format == "JPEG" and image.mode not in ("RGB", "L"):
        image = image.convert("RGB")
    original = io.BytesIO()
    if image_format == "JPEG":
        image.save(original, "JPEG", quality=90)
    else:
        image.save(original, image_format)

    thumb_image = image.copy()
    thumb_image.thumbnail((THUMB_SIZE, THUMB_SIZE))
    if thumb_image.mode != "RGB":
        # Flatten transparency onto white so the thumbnail is a plain JPEG.
        rgba = thumb_image.convert("RGBA")
        flat = Image.new("RGB", rgba.size, (255, 255, 255))
        flat.paste(rgba, mask=rgba.getchannel("A"))
        thumb_image = flat
    thumb = io.BytesIO()
    thumb_image.save(thumb, "JPEG", quality=80)

    return ProcessedImage(
        original=original.getvalue(),
        thumb=thumb.getvalue(),
        content_type=content_type,
        extension=extension,
        width=image.width,
        height=image.height,
    )


def _signature(photo_id: str, variant: str, expires: int) -> str:
    message = f"{photo_id}:{variant}:{expires}".encode()
    return hmac.new(settings.jwt_secret.encode(), b"media:" + message, hashlib.sha256).hexdigest()


def signed_path(photo_id: str, variant: str, *, now: float | None = None) -> str:
    """A link to one photo file that stops working after a few minutes."""
    expires = int((now if now is not None else time.time()) + settings.media_url_seconds)
    return f"/v1/media/{photo_id}/{variant}?exp={expires}&sig={_signature(photo_id, variant, expires)}"


def signature_is_valid(photo_id: str, variant: str, expires: int, signature: str) -> bool:
    if variant not in VARIANTS or expires < time.time():
        return False
    return hmac.compare_digest(_signature(photo_id, variant, expires), signature)
