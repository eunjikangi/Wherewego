"""Strict, bounded inline media accepted from explicit PC capture."""
import base64
import binascii
import io
import re
import warnings

from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

FRAME_BYTES = 80 * 1024
THUMBNAIL_BYTES = 24 * 1024
DATA_IMAGE = re.compile(r"data:image/(jpeg|png);base64,([A-Za-z0-9+/]+={0,2})\Z")


def validate_image(value, *, thumbnail=False):
    if not isinstance(value, str):
        raise ValueError("사진 형식을 확인하세요.")
    match = DATA_IMAGE.fullmatch(value)
    if not match or (thumbnail and match[1] != "jpeg"):
        raise ValueError("JPEG·PNG 사진만 가져올 수 있습니다.")
    limit = THUMBNAIL_BYTES if thumbnail else FRAME_BYTES
    edge = 320 if thumbnail else 1280
    if len(match[2]) > ((limit + 2) // 3) * 4:
        raise ValueError("사진 크기가 너무 큽니다. 확장에서 다시 수집하세요.")
    try:
        decoded = base64.b64decode(match[2], validate=True)
        if not decoded or len(decoded) > limit:
            raise ValueError
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(io.BytesIO(decoded)) as image:
                if image.format != ("JPEG" if match[1] == "jpeg" else "PNG"):
                    raise ValueError
                width, height = image.size
                if not (1 <= width <= edge and 1 <= height <= edge and width * height <= 2_000_000):
                    raise ValueError
                if getattr(image, "n_frames", 1) != 1:
                    raise ValueError
                image.verify()
            # JPEG verify() checks the container; load() also verifies its pixels.
            with Image.open(io.BytesIO(decoded)) as image:
                image.load()
    except (binascii.Error, OSError, UnidentifiedImageError, ValueError,
            Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise ValueError("사진 형식이나 크기를 확인하세요. 확장에서 다시 수집할 수 있습니다.") from None
    return value


class CapturedMedia(BaseModel):
    model_config = ConfigDict(extra="forbid")
    data_url: StrictStr = Field(min_length=1, max_length=112_000)
    kind: StrictStr = Field(pattern=r"^(image|video_frame)$")
    index: StrictInt = Field(default=1, ge=1, le=20)
    time_seconds: float | None = Field(default=None, ge=0, le=86_400, allow_inf_nan=False)

    @field_validator("data_url")
    @classmethod
    def actual_image(cls, value):
        return validate_image(value)

    @field_validator("time_seconds", mode="before")
    @classmethod
    def numeric_time(cls, value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))):
            raise ValueError("영상 시간 형식을 확인하세요.")
        return value
