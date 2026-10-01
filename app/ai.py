"""Private, validated AI connection settings for the organizer."""

import contextlib
import json
import os
import re
import tempfile
from pathlib import Path

from .classifier import classify_records


DEFAULT_MODEL = "gpt-4.1-mini"
MODEL_PATTERN = re.compile(r"[A-Za-z0-9._:/-]{1,80}\Z")


class AISettings:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "ai-settings.json"
        self._stored = None
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            api_key = self._validate_key(values.get("api_key"))
            model = self._validate_model(values.get("model"))
            self.path.chmod(0o600)
            self._stored = {"api_key": api_key, "model": model}
        except (OSError, ValueError, TypeError, AttributeError):
            # A missing or invalid local setting leaves environment configuration usable.
            pass

    @staticmethod
    def _validate_key(value):
        if not isinstance(value, str):
            raise ValueError("OpenAI API 키를 입력해주세요.")
        key = value.strip()
        if not key or len(key) > 512 or not key.isascii() or any(character.isspace() or not character.isprintable() for character in key):
            raise ValueError("OpenAI API 키 형식을 확인해주세요.")
        return key

    @staticmethod
    def _validate_model(value):
        if not isinstance(value, str) or not MODEL_PATTERN.fullmatch(value.strip()):
            raise ValueError("모델 이름은 영문, 숫자, 점, 콜론, 밑줄, 하이픈, 슬래시로 80자 이내로 입력해주세요.")
        return value.strip()

    def key(self):
        if self._stored:
            return self._stored["api_key"]
        return os.getenv("OPENAI_API_KEY", "").strip()

    def model(self):
        if self._stored:
            return self._stored["model"]
        model = os.getenv("OPENAI_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL
        return model if MODEL_PATTERN.fullmatch(model) else DEFAULT_MODEL

    def public(self):
        configured = bool(self.key())
        source = "app" if self._stored else "environment" if configured else "none"
        return {"configured": configured, "model": self.model(), "source": source}

    async def configure(self, api_key: str, model: str):
        api_key = self._validate_key(api_key)
        model = self._validate_model(model)
        records, warning = await classify_records(
            [{"title": "커피를 마시는 카페", "text": "", "url": ""}],
            use_ai=True,
            api_key=api_key,
            model=model,
        )
        if warning or not records or records[0].get("classification") != "ai" or records[0].get("category") != "카페":
            raise ValueError("AI 연결을 확인하지 못했습니다. API 키, 모델 사용 권한, 결제 설정을 확인해주세요.")

        values = {"api_key": api_key, "model": model}
        temporary_path = None
        try:
            descriptor, temporary_path = tempfile.mkstemp(prefix=".ai-settings-", dir=self.directory)
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                os.fchmod(handle.fileno(), 0o600)
                json.dump(values, handle, ensure_ascii=False)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary_path, self.path)
        except OSError:
            raise ValueError("AI 설정을 저장하지 못했습니다. 서버 데이터 폴더의 쓰기 권한을 확인해주세요.") from None
        finally:
            if temporary_path:
                with contextlib.suppress(OSError):
                    Path(temporary_path).unlink()
        self._stored = values
        return self.public()

    def clear(self):
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            raise ValueError("저장한 AI 설정을 지우지 못했습니다. 서버 데이터 폴더의 쓰기 권한을 확인해주세요.") from None
        self._stored = None
        return self.public()
