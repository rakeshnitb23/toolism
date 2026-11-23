"""Local counts using a public Hugging Face model's tokenizer."""

from __future__ import annotations

from typing import TYPE_CHECKING

from toolism.errors import TokenizerUnavailable
from toolism.tokenizers.base import TextTokenizer

if TYPE_CHECKING:
    from tokenizers import Tokenizer as HFTokenizer


class HuggingFaceTokenizer(TextTokenizer):
    """Count rendered tool text without provider-specific chat wrapping."""

    def __init__(self, repo_id: str) -> None:
        super().__init__()
        self.name = f"huggingface:{repo_id}"
        self._tokenizer = self._load(repo_id)

    @staticmethod
    def _load(repo_id: str) -> HFTokenizer:
        try:
            from huggingface_hub import hf_hub_download
            from huggingface_hub.errors import (
                EntryNotFoundError,
                GatedRepoError,
                HFValidationError,
                LocalEntryNotFoundError,
                OfflineModeIsEnabled,
                RepositoryNotFoundError,
            )
            from tokenizers import Tokenizer as HFTokenizer
        except ImportError as exc:
            raise TokenizerUnavailable(
                "the huggingface tokenizer needs the Hugging Face extra",
                hint="pip install 'toolism[huggingface]'",
            ) from exc

        try:
            path = hf_hub_download(repo_id, "tokenizer.json", token=False)
        except GatedRepoError as exc:
            raise TokenizerUnavailable(
                f"Hugging Face repository {repo_id!r} is gated",
                hint="choose a public model with an accessible tokenizer.json",
            ) from exc
        except (RepositoryNotFoundError, HFValidationError) as exc:
            raise TokenizerUnavailable(
                f"Hugging Face repository {repo_id!r} was not found or is not public"
            ) from exc
        except (LocalEntryNotFoundError, OfflineModeIsEnabled) as exc:
            raise TokenizerUnavailable(
                f"tokenizer.json for {repo_id!r} is not available in the Hugging Face cache",
                hint="cache the public tokenizer while online or use a different tokenizer",
            ) from exc
        except EntryNotFoundError as exc:
            raise TokenizerUnavailable(
                f"Hugging Face repository {repo_id!r} does not provide tokenizer.json"
            ) from exc
        except Exception:
            # Hub releases use different HTTP clients and may surface transport errors directly.
            # Keep upstream exception details (such as proxy diagnostics) out of CLI output.
            raise TokenizerUnavailable(
                f"could not download tokenizer.json for {repo_id!r}"
            ) from None

        try:
            tokenizer = HFTokenizer.from_file(path)
            # A tokenizer file can carry model-input length settings; count the raw text instead.
            tokenizer.no_truncation()
            tokenizer.no_padding()
            return tokenizer
        except Exception as exc:
            # The native extension raises a generic Exception for an invalid tokenizer file.
            raise TokenizerUnavailable(f"could not load tokenizer.json for {repo_id!r}") from exc

    def count_text(self, text: str) -> int:
        return len(self._tokenizer.encode(text, add_special_tokens=False).ids)
