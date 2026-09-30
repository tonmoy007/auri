"""Tests for the Ollama embedding client.

The embedding server is faked with ``httpx.MockTransport``; nothing here touches the
network. Texts are synthetic. The client embeds the study library (and, for a query,
a user's words), so a recurring check is that no error message ever carries the text
that was being embedded.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from pathlib import Path

import httpx
import numpy as np
import pytest
from app.exceptions import PriestIndexError
from app.priest import embedder as embedder_module
from app.priest.embedder import (
    MODEL_REGISTRY,
    EmbedModelInfo,
    OllamaEmbedder,
)

BASE_URL = "http://ollama.test:11434"
SECRET = "a-very-private-question-about-my-manager"


def _vector_for(text: str, dim: int) -> list[float]:
    """A deterministic, deliberately un-normalised vector for *text*."""
    seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "big")
    return (np.random.default_rng(seed).normal(size=dim) * 3.0).tolist()


class FakeOllama:
    """A scriptable stand-in for the Ollama HTTP API that records every request."""

    def __init__(
        self,
        *,
        dim: int = 768,
        digest: str | None = "sha256:aaa",
        embed_status: int = 200,
        show_extra: dict[str, object] | None = None,
        tags_digest: str | None = None,
    ) -> None:
        self.dim = dim
        self.digest = digest
        self.embed_status = embed_status
        self.show_extra = show_extra or {}
        self.tags_digest = tags_digest
        self.calls: list[httpx.Request] = []
        self.override: Callable[[httpx.Request], httpx.Response] | None = None

    def paths(self) -> list[str]:
        """The request paths seen so far, in order."""
        return [call.url.path for call in self.calls]

    def bodies(self, path: str) -> list[dict[str, object]]:
        """The JSON bodies sent to *path*."""
        return [json.loads(c.content) for c in self.calls if c.url.path == path]

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request)
        if self.override is not None:
            return self.override(request)
        path = request.url.path
        if path == "/api/show":
            body: dict[str, object] = dict(self.show_extra)
            if self.digest is not None:
                body["digest"] = self.digest
            return httpx.Response(200, json=body)
        if path == "/api/tags":
            models = [{"name": "nomic-embed-text:latest", "digest": self.tags_digest}]
            return httpx.Response(200, json={"models": models})
        if path == "/api/embed":
            if self.embed_status != 200:
                return httpx.Response(self.embed_status, json={"error": "nope"})
            texts = json.loads(request.content)["input"]
            vectors = [_vector_for(t, self.dim) for t in texts]
            return httpx.Response(200, json={"embeddings": vectors})
        if path == "/api/embeddings":
            prompt = json.loads(request.content)["prompt"]
            return httpx.Response(
                200, json={"embedding": _vector_for(prompt, self.dim)}
            )
        return httpx.Response(404)


def _make(
    fake: FakeOllama,
    model: str = "nomic-embed-text",
    cache_dir: Path | None = None,
    base_url: str = BASE_URL,
) -> OllamaEmbedder:
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake))
    return OllamaEmbedder(base_url, model, client=client, cache_dir=cache_dir)


# ── Registry ────────────────────────────────────────────────────────────


def test_registry_lists_the_three_candidate_models_with_their_prefixes() -> None:
    # Arrange
    mxbai_query = "Represent this sentence for searching relevant passages: "

    # Act
    nomic = MODEL_REGISTRY["nomic-embed-text"]
    mxbai = MODEL_REGISTRY["mxbai-embed-large"]
    bge = MODEL_REGISTRY["bge-large"]

    # Assert
    assert (nomic.dim, nomic.doc_prefix, nomic.query_prefix) == (
        768,
        "search_document: ",
        "search_query: ",
    )
    assert (mxbai.dim, mxbai.doc_prefix, mxbai.query_prefix) == (1024, "", mxbai_query)
    assert (bge.dim, bge.doc_prefix, bge.query_prefix) == (1024, "", mxbai_query)
    assert all(spec.window_tokens > 0 for spec in MODEL_REGISTRY.values())


def test_an_unknown_model_is_refused_at_construction() -> None:
    # Arrange
    fake = FakeOllama()

    # Act / Assert
    with pytest.raises(PriestIndexError):
        _make(fake, model="some-other-model")


# ── Prefixes, shape and normalisation ───────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("model", "doc_prefix", "query_prefix"),
    [
        ("nomic-embed-text", "search_document: ", "search_query: "),
        (
            "mxbai-embed-large",
            "",
            "Represent this sentence for searching relevant passages: ",
        ),
        ("bge-large", "", "Represent this sentence for searching relevant passages: "),
    ],
)
async def test_each_role_gets_its_models_prefix(
    model: str, doc_prefix: str, query_prefix: str
) -> None:
    # Arrange
    fake = FakeOllama(dim=MODEL_REGISTRY[model].dim)
    embedder = _make(fake, model=model)

    # Act
    await embedder.embed_documents(["grief and loss"])
    await embedder.embed_query("how to accept loss")

    # Assert
    sent = [body["input"] for body in fake.bodies("/api/embed")]
    assert sent == [
        [f"{doc_prefix}grief and loss"],
        [f"{query_prefix}how to accept loss"],
    ]
    assert all(body["model"] == model for body in fake.bodies("/api/embed"))


@pytest.mark.asyncio
async def test_documents_come_back_float32_unit_length_and_in_order() -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake)
    texts = ["alpha note", "beta note", "gamma note"]

    # Act
    matrix = await embedder.embed_documents(texts)

    # Assert
    assert matrix.shape == (3, 768)
    assert matrix.dtype == np.float32
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)
    raw = np.asarray(_vector_for("search_document: beta note", 768), dtype=np.float32)
    assert np.allclose(matrix[1], raw / np.linalg.norm(raw), atol=1e-5)


@pytest.mark.asyncio
async def test_a_query_is_a_single_unit_vector() -> None:
    # Arrange
    embedder = _make(FakeOllama())

    # Act
    vector = await embedder.embed_query("what is forgiveness")

    # Assert
    assert vector.shape == (768,)
    assert vector.dtype == np.float32
    assert np.linalg.norm(vector) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.asyncio
async def test_no_texts_means_an_empty_matrix_and_no_request() -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake)

    # Act
    matrix = await embedder.embed_documents([])

    # Assert
    assert matrix.shape == (0, 768)
    assert fake.calls == []


@pytest.mark.asyncio
async def test_documents_are_sent_in_batches() -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake)
    texts = [f"note number {i}" for i in range(70)]

    # Act
    matrix = await embedder.embed_documents(texts)

    # Assert
    sizes = [len(body["input"]) for body in fake.bodies("/api/embed")]  # type: ignore[arg-type]
    assert matrix.shape == (70, 768)
    assert len(sizes) == 3 and sum(sizes) == 70 and max(sizes) <= 32


# ── Validation ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_wrong_dimension_raises_without_echoing_the_text() -> None:
    # Arrange
    embedder = _make(FakeOllama(dim=3))

    # Act
    with pytest.raises(PriestIndexError) as caught:
        await embedder.embed_documents([SECRET])

    # Assert
    assert SECRET not in str(caught.value)
    assert "768" in str(caught.value)


@pytest.mark.asyncio
async def test_a_wrong_dimension_on_a_query_raises() -> None:
    # Arrange
    embedder = _make(FakeOllama(dim=10))

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await embedder.embed_query(SECRET)


@pytest.mark.asyncio
async def test_a_mixed_length_batch_raises() -> None:
    # Arrange
    fake = FakeOllama()

    def ragged(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"digest": "sha256:aaa"})
        return httpx.Response(200, json={"embeddings": [[1.0] * 768, [1.0] * 767]})

    fake.override = ragged
    embedder = _make(fake)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await embedder.embed_documents(["one", "two"])


@pytest.mark.asyncio
async def test_a_short_answer_count_raises() -> None:
    # Arrange
    fake = FakeOllama()

    def short(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"digest": "sha256:aaa"})
        return httpx.Response(200, json={"embeddings": [[1.0] * 768]})

    fake.override = short
    embedder = _make(fake)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await embedder.embed_documents(["one", "two"])


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [[0.0] * 768, [float("nan")] * 768])
async def test_a_zero_or_non_finite_vector_raises(bad: list[float]) -> None:
    # Arrange
    fake = FakeOllama()

    def broken(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"digest": "sha256:aaa"})
        return httpx.Response(200, content=json.dumps({"embeddings": [bad]}).encode())

    fake.override = broken
    embedder = _make(fake)

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await embedder.embed_documents(["one"])


# ── Errors never carry the text ─────────────────────────────────────────


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(500, text="boom"),
        httpx.Response(200, text="not json at all"),
        httpx.Response(200, json={"unexpected": True}),
        httpx.Response(200, json={"embeddings": "nope"}),
    ],
)
async def test_server_faults_raise_a_domain_error_with_no_text(
    response: httpx.Response,
) -> None:
    # Arrange
    fake = FakeOllama()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/show":
            return httpx.Response(200, json={"digest": "sha256:aaa"})
        return response

    fake.override = handler
    embedder = _make(fake)

    # Act
    with pytest.raises(PriestIndexError) as docs:
        await embedder.embed_documents([SECRET])
    with pytest.raises(PriestIndexError) as query:
        await embedder.embed_query(SECRET)

    # Assert
    assert SECRET not in str(docs.value) and SECRET not in str(query.value)


@pytest.mark.asyncio
async def test_a_connection_failure_raises_a_domain_error_with_no_text() -> None:
    # Arrange
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(refuse))
    embedder = OllamaEmbedder(BASE_URL, "nomic-embed-text", client=client)

    # Act
    with pytest.raises(PriestIndexError) as caught:
        await embedder.embed_query(SECRET)

    # Assert
    assert SECRET not in str(caught.value)


# ── Cache ───────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_cache_hit_makes_no_request_at_all(tmp_path: Path) -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake, cache_dir=tmp_path)
    first = await embedder.embed_documents(["cached note"])
    seen = len(fake.calls)

    # Act
    second = await embedder.embed_documents(["cached note"])

    # Assert
    assert len(fake.calls) == seen
    assert np.array_equal(first, second)


@pytest.mark.asyncio
async def test_a_warm_cache_skips_embedding_for_a_fresh_client(tmp_path: Path) -> None:
    # Arrange
    await _make(FakeOllama(), cache_dir=tmp_path).embed_documents(["a", "b"])
    fake = FakeOllama()
    embedder = _make(fake, cache_dir=tmp_path)

    # Act
    matrix = await embedder.embed_documents(["a", "b"])

    # Assert: the digest is still read, because it is part of the key
    assert matrix.shape == (2, 768)
    assert "/api/embed" not in fake.paths()


@pytest.mark.asyncio
async def test_only_the_misses_are_embedded(tmp_path: Path) -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake, cache_dir=tmp_path)
    await embedder.embed_documents(["old one"])
    fake.calls.clear()

    # Act
    matrix = await embedder.embed_documents(["old one", "new one"])

    # Assert
    assert matrix.shape == (2, 768)
    assert [b["input"] for b in fake.bodies("/api/embed")] == [
        ["search_document: new one"]
    ]


@pytest.mark.asyncio
async def test_a_new_model_digest_invalidates_the_cache(tmp_path: Path) -> None:
    # Arrange
    await _make(FakeOllama(digest="sha256:old"), cache_dir=tmp_path).embed_documents(
        ["note"]
    )
    fake = FakeOllama(digest="sha256:new")

    # Act
    await _make(fake, cache_dir=tmp_path).embed_documents(["note"])

    # Assert
    assert "/api/embed" in fake.paths()


@pytest.mark.asyncio
async def test_a_corrupt_cache_entry_is_re_embedded(tmp_path: Path) -> None:
    # Arrange
    embedder = _make(FakeOllama(), cache_dir=tmp_path)
    await embedder.embed_documents(["note"])
    for entry in tmp_path.iterdir():
        entry.write_bytes(b"garbage")
    fake = FakeOllama()

    # Act
    matrix = await _make(fake, cache_dir=tmp_path).embed_documents(["note"])

    # Assert
    assert matrix.shape == (1, 768)
    assert "/api/embed" in fake.paths()


@pytest.mark.asyncio
async def test_cache_file_names_do_not_reveal_the_text(tmp_path: Path) -> None:
    # Arrange
    embedder = _make(FakeOllama(), cache_dir=tmp_path)

    # Act
    await embedder.embed_documents([SECRET])

    # Assert
    names = [p.name for p in tmp_path.iterdir()]
    assert names and all(SECRET not in name for name in names)


@pytest.mark.asyncio
async def test_a_query_is_never_written_to_the_cache(tmp_path: Path) -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake, cache_dir=tmp_path)

    # Act
    await embedder.embed_query(SECRET)
    await embedder.embed_query(SECRET)

    # Assert: nothing stored, so the second query went to the server again
    assert list(tmp_path.iterdir()) == []
    assert len(fake.bodies("/api/embed")) == 2


# ── Fallback transport ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_a_404_on_embed_falls_back_to_one_at_a_time() -> None:
    # Arrange
    fake = FakeOllama(embed_status=404)
    embedder = _make(fake)

    # Act
    matrix = await embedder.embed_documents(["one", "two", "three"])

    # Assert
    assert matrix.shape == (3, 768)
    legacy = fake.bodies("/api/embeddings")
    assert [b["prompt"] for b in legacy] == [
        "search_document: one",
        "search_document: two",
        "search_document: three",
    ]
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)


@pytest.mark.asyncio
async def test_the_fallback_is_remembered_for_later_calls() -> None:
    # Arrange
    fake = FakeOllama(embed_status=404)
    embedder = _make(fake)
    await embedder.embed_documents(["one"])
    fake.calls.clear()

    # Act
    query = await embedder.embed_query("two")

    # Assert
    assert query.shape == (768,)
    assert fake.paths() == ["/api/embeddings"]


# ── Model digest ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_model_info_reads_the_digest_from_show() -> None:
    # Arrange
    fake = FakeOllama(digest="sha256:0a1b2c")
    embedder = _make(fake)

    # Act
    info = await embedder.model_info()

    # Assert
    assert info == EmbedModelInfo(
        name="nomic-embed-text", digest="sha256:0a1b2c", dim=768
    )
    assert fake.bodies("/api/show") == [{"model": "nomic-embed-text"}]


@pytest.mark.asyncio
async def test_model_info_falls_back_to_the_tag_list_when_show_has_no_digest() -> None:
    # Arrange
    fake = FakeOllama(digest=None, tags_digest="sha256:fromtags")
    embedder = _make(fake)

    # Act
    info = await embedder.model_info()

    # Assert
    assert info.digest == "sha256:fromtags"


@pytest.mark.asyncio
async def test_model_info_raises_when_no_digest_can_be_found() -> None:
    # Arrange
    embedder = _make(FakeOllama(digest=None, tags_digest=None))

    # Act / Assert
    with pytest.raises(PriestIndexError):
        await embedder.model_info()


@pytest.mark.asyncio
async def test_model_info_is_reused_until_it_expires(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # Arrange
    clock = [1000.0]
    monkeypatch.setattr(embedder_module, "monotonic", lambda: clock[0])
    fake = FakeOllama()
    embedder = _make(fake)

    # Act
    await embedder.model_info()
    await embedder.model_info()
    cached_calls = fake.paths().count("/api/show")
    clock[0] += 3600
    await embedder.model_info()

    # Assert
    assert cached_calls == 1
    assert fake.paths().count("/api/show") == 2


@pytest.mark.asyncio
async def test_a_pulled_model_tag_resolves_to_the_registry_name() -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake, model="nomic-embed-text:latest")

    # Act
    info = await embedder.model_info()
    await embedder.embed_query("hello there")

    # Assert
    assert info.dim == 768
    assert fake.bodies("/api/embed")[0]["input"] == ["search_query: hello there"]
    assert fake.bodies("/api/embed")[0]["model"] == "nomic-embed-text:latest"


# ── Base URL ────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_the_configured_base_url_is_used_as_given() -> None:
    # Arrange
    fake = FakeOllama()
    embedder = _make(fake, base_url="http://gpu-box.internal:9999/")

    # Act
    await embedder.embed_query("hello")

    # Assert
    assert {(c.url.host, c.url.port) for c in fake.calls} == {
        ("gpu-box.internal", 9999)
    }
    assert "/api/embed" in fake.paths()
