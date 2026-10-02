"""Private, validated AI connection settings for the organizer."""

import contextlib
import json
import os
import tempfile
from pathlib import Path

from .classifier import DEFAULT_MODELS, MODEL_PATTERN, classify_records, normalize_model, normalize_provider


DEFAULT_MODEL = DEFAULT_MODELS["openai"]


class AISettings:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.path = self.directory / "ai-settings.json"
        self._stored = None
        try:
            values = json.loads(self.path.read_text(encoding="utf-8"))
            provider = normalize_provider(values.get("provider", "openai"))
            api_key = self._validate_key(values.get("api_key"))
            model = self._validate_model(values.get("model"), provider)
            self.path.chmod(0o600)
            self._stored = {"provider": provider, "api_key": api_key, "model": model}
        except (OSError, ValueError, TypeError, AttributeError):
            # A missing or invalid local setting leaves environment configuration usable.
            pass

    @staticmethod
    def _validate_key(value):
        if not isinstance(value, str):
            raise ValueError("AI API 키를 입력해주세요.")
        key = value.strip()
        if not key or len(key) > 512 or not key.isascii() or any(character.isspace() or not character.isprintable() for character in key):
            raise ValueError("AI API 키 형식을 확인해주세요.")
        return key

    @staticmethod
    def _validate_model(value, provider="openai"):
        return normalize_model(value, provider)

    def provider(self):
        if self._stored:
            return self._stored["provider"]
        try:
            return normalize_provider(os.getenv("AI_PROVIDER", "openai"))
        except ValueError:
            return "openai"

    def key(self):
        if self._stored:
            return self._stored["api_key"]
        return os.getenv(f"{self.provider().upper()}_API_KEY", "").strip()

    def model(self):
        if self._stored:
            return self._stored["model"]
        provider = self.provider()
        model = os.getenv(f"{provider.upper()}_MODEL", "").strip() or DEFAULT_MODELS[provider]
        try:
            return self._validate_model(model, provider)
        except ValueError:
            return DEFAULT_MODELS[provider]

    def public(self):
        configured = bool(self.key())
        source = "app" if self._stored else "environment" if configured else "none"
        return {"configured": configured, "provider": self.provider(), "model": self.model(), "source": source}

    async def configure(self, api_key: str, model: str = None, provider: str = "openai"):
        provider = normalize_provider(provider)
        api_key = self._validate_key(api_key)
        model = self._validate_model(DEFAULT_MODELS[provider] if model is None else model, provider)
        records, warning = await classify_records(
            [{"title": "커피를 마시는 카페", "text": "", "url": ""}],
            use_ai=True,
            api_key=api_key,
            model=model,
            provider=provider,
        )
        if warning or not records or records[0].get("classification") != "ai" or records[0].get("category") != "카페":
            raise ValueError("AI 연결을 확인하지 못했습니다. API 키, 모델 사용 권한, 결제 설정을 확인해주세요.")

        values = {"provider": provider, "api_key": api_key, "model": model}
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
