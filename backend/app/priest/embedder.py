"""Async client for Ollama embeddings, used to build and query the study index.

Vectors are L2-normalised float32, so cosine similarity is a dot product. The model's
digest is part of the index manifest and of the document cache key: an index built
with one set of weights must never be searched with another.

Two rules follow from privacy. Only *documents* (library text) are cached on disk; a
query is a user's own words and is never stored. And no error raised here carries the
text being embedded.
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any, Final

import httpx
import numpy as np
import numpy.typing as npt

from app.exceptions import PriestIndexError

logger = logging.getLogger(__name__)

Vectors = npt.NDArray[np.float32]

_BATCH_SIZE: Final = 32
_INFO_TTL_SECONDS: Final = 60.0
_TIMEOUT: Final = httpx.Timeout(60.0, connect=5.0)
_EMBED_PATH: Final = "/api/embed"
_LEGACY_PATH: Final = "/api/embeddings"
_SHOW_PATH: Final = "/api/show"
_TAGS_PATH: Final = "/api/tags"
_MXBAI_QUERY: Final = "Represent this sentence for searching relevant passages: "


@dataclass(frozen=True)
class EmbedModelInfo:
    """Which embedding model, exactly, an index was built with."""

    name: str
    digest: str
    dim: int


@dataclass(frozen=True)
class ModelSpec:
    """What is known about one embedding model before any request is made."""

    dim: int
    window_tokens: int
    doc_prefix: str
    query_prefix: str


# Window sizes are Ollama's default context for each model, not the model's maximum:
# the chunker caps chunks at 450 tokens, so all three fit.
MODEL_REGISTRY: Final[dict[str, ModelSpec]] = {
    "nomic-embed-text": ModelSpec(768, 2048, "search_document: ", "search_query: "),
    "mxbai-embed-large": ModelSpec(1024, 512, "", _MXBAI_QUERY),
    "bge-large": ModelSpec(1024, 512, "", _MXBAI_QUERY),
}


def _canonical_name(model: str) -> str:
    """Drop Ollama's implicit ``:latest`` tag so a pulled model matches the registry."""
    return model.removesuffix(":latest")


def _cache_key(digest: str, prefix: str, text: str) -> str:
    return hashlib.sha256(f"{digest}|{prefix}|{text}".encode()).hexdigest()


def _json_object(response: httpx.Response) -> dict[str, Any]:
    """The reply body as a JSON object, or a domain error that names no content."""
    if not response.is_success:
        raise PriestIndexError(f"embedding server returned HTTP {response.status_code}")
    try:
        body = response.json()
    except ValueError as exc:
        raise PriestIndexError(
            "embedding server sent a reply that is not JSON"
        ) from exc
    if not isinstance(body, dict):
        raise PriestIndexError("embedding server sent an unexpected reply")
    return body


def _to_matrix(rows: object, expected: int, dim: int) -> Vectors:
    """Validate raw vectors and return them L2-normalised as a float32 matrix."""
    if not isinstance(rows, list) or len(rows) != expected:
        raise PriestIndexError("embedding server returned the wrong number of vectors")
    matrix = np.empty((expected, dim), dtype=np.float32)
    for i, row in enumerate(rows):
        try:
            vector = np.asarray(row, dtype=np.float32)
        except (TypeError, ValueError) as exc:
            raise PriestIndexError("embedding server sent a malformed vector") from exc
        if vector.ndim != 1 or vector.shape[0] != dim:
            size = vector.shape[0] if vector.ndim == 1 else vector.ndim
            raise PriestIndexError(
                f"embedding has dimension {size}, expected {dim}; "
                "the model or the registry entry is wrong"
            )
        norm = float(np.linalg.norm(vector.astype(np.float64)))
        if not np.isfinite(norm) or norm == 0.0:
            raise PriestIndexError("embedding server sent an unusable vector")
        matrix[i] = vector / norm
    return matrix


class OllamaEmbedder:
    """Embeds texts through an Ollama server whose address comes from settings."""

    def __init__(
        self,
        base_url: str,
        model: str,
        *,
        client: httpx.AsyncClient | None = None,
        cache_dir: Path | None = None,
    ) -> None:
        """Create an embedder for *model*.

        Args:
            base_url: The Ollama address, used as given and never built from user input.
            model: An Ollama model name that is in ``MODEL_REGISTRY``.
            client: An HTTP client to use; one is created, and owned, when omitted.
            cache_dir: Where to keep document vectors; ``None`` turns the cache off.

        Raises:
            PriestIndexError: If *model* is not in the registry, so its dimension and
                prefixes are unknown.
        """
        name = _canonical_name(model)
        if name not in MODEL_REGISTRY:
            raise PriestIndexError("embedding model is not in the registry")
        self._base = base_url.rstrip("/")
        self._model = model
        self._name = name
        self._spec = MODEL_REGISTRY[name]
        self._owns_client = client is None
        self._client = (
            client if client is not None else httpx.AsyncClient(timeout=_TIMEOUT)
        )
        self._cache_dir = cache_dir
        self._legacy = False
        self._info: EmbedModelInfo | None = None
        self._info_at = 0.0

    async def aclose(self) -> None:
        """Close the HTTP client if this embedder created it."""
        if self._owns_client:
            await self._client.aclose()

    async def model_info(self) -> EmbedModelInfo:
        """The model's name, digest and dimension, re-read from Ollama each minute."""
        now = monotonic()
        if self._info is None or now - self._info_at >= _INFO_TTL_SECONDS:
            digest = await self._fetch_digest()
            self._info = EmbedModelInfo(self._name, digest, self._spec.dim)
            self._info_at = now
        return self._info

    async def embed_documents(self, texts: Sequence[str]) -> Vectors:
        """Embed library passages, reusing cached vectors.

        Returns:
            A ``(len(texts), dim)`` float32 matrix of unit vectors, in input order.
        """
        out = np.empty((len(texts), self._spec.dim), dtype=np.float32)
        if not texts:
            return out
        prefix = self._spec.doc_prefix
        digest = (await self.model_info()).digest if self._cache_dir else ""
        keys = [_cache_key(digest, prefix, t) for t in texts]
        missing = [i for i, key in enumerate(keys) if not self._cache_read(key, out, i)]
        for start in range(0, len(missing), _BATCH_SIZE):
            batch = missing[start : start + _BATCH_SIZE]
            rows = await self._embed_raw([prefix + texts[i] for i in batch])
            matrix = _to_matrix(rows, len(batch), self._spec.dim)
            for slot, i in enumerate(batch):
                out[i] = matrix[slot]
                self._cache_write(keys[i], matrix[slot])
        return out

    async def embed_query(self, text: str) -> Vectors:
        """Embed a question. Never cached and never stored: it is the user's words."""
        rows = await self._embed_raw([self._spec.query_prefix + text])
        return _to_matrix(rows, 1, self._spec.dim)[0]

    async def _embed_raw(self, inputs: list[str]) -> object:
        """Send *inputs* to ``/api/embed``, or one by one to the older endpoint.

        Returns the reply's raw vector list; ``_to_matrix`` validates it.
        """
        if not self._legacy:
            response = await self._post(
                _EMBED_PATH, {"model": self._model, "input": inputs}
            )
            if response.status_code != 404:
                return _json_object(response).get("embeddings")
            self._legacy = True
        rows: list[object] = []
        for prompt in inputs:
            reply = await self._post(
                _LEGACY_PATH, {"model": self._model, "prompt": prompt}
            )
            rows.append(_json_object(reply).get("embedding"))
        return rows

    async def _post(self, path: str, payload: dict[str, Any]) -> httpx.Response:
        try:
            return await self._client.post(f"{self._base}{path}", json=payload)
        except httpx.HTTPError as exc:
            raise PriestIndexError("embedding server is unreachable") from exc

    async def _fetch_digest(self) -> str:
        """Read the digest from ``/api/show``, else from the tag list."""
        show = _json_object(await self._post(_SHOW_PATH, {"model": self._model}))
        digest = show.get("digest")
        if isinstance(digest, str) and digest:
            return digest
        try:
            tags = _json_object(await self._client.get(f"{self._base}{_TAGS_PATH}"))
        except httpx.HTTPError as exc:
            raise PriestIndexError("embedding server is unreachable") from exc
        wanted = {self._model, self._name, f"{self._name}:latest"}
        for entry in tags.get("models") or []:
            found = entry.get("digest") if isinstance(entry, dict) else None
            if isinstance(entry, dict) and entry.get("name") in wanted and found:
                return str(found)
        raise PriestIndexError("embedding model digest could not be determined")

    def _cache_read(self, key: str, out: Vectors, row: int) -> bool:
        """Fill ``out[row]`` from the cache; ``False`` on a miss or a damaged entry."""
        if self._cache_dir is None:
            return False
        try:
            vector = np.load(self._cache_dir / f"{key}.npy", allow_pickle=False)
        except (OSError, ValueError, EOFError):
            return False
        if vector.shape != (self._spec.dim,) or vector.dtype != np.float32:
            return False
        out[row] = vector
        return True

    def _cache_write(self, key: str, vector: Vectors) -> None:
        """Store one vector atomically; a cache that cannot be written is only slower."""
        if self._cache_dir is None:
            return
        try:
            self._cache_dir.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                dir=self._cache_dir, suffix=".tmp", delete=False
            ) as handle:
                np.save(handle, vector)
            os.replace(handle.name, self._cache_dir / f"{key}.npy")
        except OSError:
            logger.warning("embedding cache write failed; continuing without it")
