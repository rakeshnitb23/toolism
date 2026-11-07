from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from typing import Any, ClassVar

import pytest

from toolism.measurement import measure
from toolism.models import ToolDef, ToolFormat, ToolSet
from toolism.pipeline import slim
from toolism.tokenizers import anthropic_tokenizer as anthropic_module
from toolism.tokenizers.anthropic_tokenizer import AnthropicTokenizer

TOOLS = (
    ToolDef("alpha", "private-schema-canary", {"type": "object", "title": "A"}),
    ToolDef("beta", None, {"type": "object", "properties": {"q": {"type": "string"}}}),
)
TOOLSET = ToolSet(TOOLS, ToolFormat.MCP)


class CountingClient:
    base_url = "https://api.anthropic.com"
    default_headers: ClassVar[dict[str, str]] = {"anthropic-version": "2023-06-01"}

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(count_tokens=self.count)

    def count(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        items = kwargs.get("tools", [])
        tokens = (
            100
            if not items
            else 105
            if items == [anthropic_module._PROBE]
            else (
                123
                + len(json.dumps(items, separators=(",", ":")))
                + 31 * (len(items) - 1)
                + sum((i + 1) * len(t["name"]) for i, t in enumerate(items))
            )
        )
        return SimpleNamespace(input_tokens=tokens)


def tokenizer(
    monkeypatch: pytest.MonkeyPatch, model: str = "test-model"
) -> tuple[AnthropicTokenizer, CountingClient]:
    client = CountingClient()
    monkeypatch.setattr(AnthropicTokenizer, "_make_client", staticmethod(lambda: client))
    return AnthropicTokenizer(model), client


@pytest.fixture(autouse=True)
def cache_directory(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.setenv("TOOLISM_CACHE_DIR", str(tmp_path / "cache"))
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    return tmp_path / "cache"


@pytest.mark.parametrize("operation", ["marginals", "measure", "slim"])
def test_second_tokenizer_makes_zero_calls(monkeypatch: pytest.MonkeyPatch, operation: str) -> None:
    def run(counter: AnthropicTokenizer) -> Any:
        if operation == "marginals":
            return [counter.count_tool(t) for t in TOOLS]
        if operation == "measure":
            return measure(TOOLSET, counter)
        return slim(TOOLSET, rules=[], tokenizer=counter).report

    first, cold = tokenizer(monkeypatch)
    expected = run(first)
    assert cold.calls
    second, warm = tokenizer(monkeypatch)
    assert run(second) == expected
    assert warm.calls == []


def test_injected_client_remains_memory_only(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path
) -> None:
    first_client, second_client = CountingClient(), CountingClient()
    first = AnthropicTokenizer("m", client=first_client)
    second = AnthropicTokenizer("m", client=second_client)
    assert first.count_tool(TOOLS[0]) == second.count_tool(TOOLS[0])
    assert len(second_client.calls) == 2
    assert not cache_directory.exists()


def test_selective_tool_and_bundle_invalidation(monkeypatch: pytest.MonkeyPatch) -> None:
    first, _ = tokenizer(monkeypatch)
    measure(TOOLSET, first)
    changed = TOOLSET.replace_tools([TOOLS[0].replace(description="changed"), TOOLS[1]])
    second, client = tokenizer(monkeypatch)
    measure(changed, second)
    assert len(client.calls) == 2  # changed singleton and aggregate; calibration/B survive
    third, reordered = tokenizer(monkeypatch)
    third.count_tools(list(reversed(TOOLS)))
    third.count_tools([TOOLS[0], TOOLS[0]])
    assert len(reordered.calls) == 2


@pytest.mark.parametrize("identity", ["model", "version", "sdk", "recipe", "endpoint", "header"])
def test_identity_changes_invalidate(monkeypatch: pytest.MonkeyPatch, identity: str) -> None:
    first, _ = tokenizer(monkeypatch)
    measure(TOOLSET, first)
    model = "test-model"
    if identity == "model":
        model = "other-model"
    elif identity == "version":
        monkeypatch.setattr(anthropic_module, "__version__", "next-version")
    elif identity == "sdk":
        monkeypatch.setattr(anthropic_module, "version", lambda _: "next-sdk")
    elif identity == "recipe":
        monkeypatch.setattr(anthropic_module, "_COUNT_RECIPE", 2)
    elif identity == "endpoint":
        monkeypatch.setattr(CountingClient, "base_url", "https://other.example/api")
    else:
        monkeypatch.setattr(CountingClient, "default_headers", {"anthropic-version": "new"})
    second, client = tokenizer(monkeypatch, model)
    measure(TOOLSET, second)
    assert len(client.calls) == 5


def test_payload_digest_handles_mutation_and_key_order(monkeypatch: pytest.MonkeyPatch) -> None:
    schema: dict[str, Any] = {"type": "object", "title": "first"}
    tool = ToolDef("mutable", input_schema=schema)
    first, client = tokenizer(monkeypatch)
    first.count_tool(tool)
    schema["title"] = "second"
    first.count_tool(tool)
    assert len(client.calls) == 3
    equivalent = ToolDef("mutable", input_schema={"title": "second", "type": "object"})
    assert tool.fingerprint() != equivalent.fingerprint()  # old object memoized before mutation
    second, reordered = tokenizer(monkeypatch)
    second.count_tool(equivalent)
    assert len(reordered.calls) == 1  # new wire order, same probe


@pytest.mark.parametrize("setting", [None, "", "custom-headers"])
def test_disabled_cache_never_looks_up_sdk_or_creates_files(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path, setting: str | None
) -> None:
    if setting is None:
        monkeypatch.delenv("TOOLISM_CACHE_DIR")
    elif setting == "":
        monkeypatch.setenv("TOOLISM_CACHE_DIR", "")
    else:
        monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "Anthropic-Version: custom")

    def unexpected(_: str) -> str:
        pytest.fail("Disabled persistence must not look up SDK metadata")

    monkeypatch.setattr(anthropic_module, "version", unexpected)
    tok, client = tokenizer(monkeypatch)
    tok.count_tool(TOOLS[0])
    assert len(client.calls) == 2
    assert not cache_directory.exists()


def test_enabled_expiry_discards_all_memory(monkeypatch: pytest.MonkeyPatch) -> None:
    from toolism.tokenizers import _cache

    now = [1000.0]
    monkeypatch.setattr(_cache, "time", lambda: now[0])
    counter, client = tokenizer(monkeypatch)
    original = measure(TOOLSET, counter)
    assert len(client.calls) == 5
    now[0] += _cache.TTL_SECONDS
    assert measure(TOOLSET, counter) == original
    assert len(client.calls) == 10


def test_provider_errors_are_not_cached(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path
) -> None:
    from toolism.errors import TokenizerUnavailable

    counter, client = tokenizer(monkeypatch)

    def fail_probe(**kwargs: Any) -> Any:
        if kwargs.get("tools") == [anthropic_module._PROBE]:
            raise RuntimeError("probe unavailable")
        return client.count(**kwargs)

    client.messages.count_tokens = fail_probe
    with pytest.raises(TokenizerUnavailable, match="probe unavailable"):
        counter.count_tool(TOOLS[0])
    assert not (cache_directory / "toolism-token-counts-v1.json").exists()
    second, next_client = tokenizer(monkeypatch)
    second.count_tool(TOOLS[0])
    assert len(next_client.calls) == 2


def test_snapshot_strict_validation_and_recovery(tmp_path: Path) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    valid = SnapshotCache(tmp_path, "context", clock=lambda: 1000.0)
    key = valid.key("marginal", "payload")
    assert key is not None

    def document(value: Any, *, created: Any = 1000, version: Any = 1) -> bytes:
        data = {"version": version, "created": created, "entries": {key: value}}
        checksum = hashlib.sha256(json.dumps(data, separators=(",", ":")).encode()).hexdigest()
        return json.dumps({**data, "checksum": checksum}).encode()

    bad = [
        b"",
        b"{",
        b"\xff",
        b"[]",
        b'{"version":1,"version":1}',
        json.dumps({"version": 1, "created": 1000, "entries": {key: 1}}).encode(),
        document(1, created=2000),
        document(1, created=True),
        document(1, version=True),
        document(1, version=2),
        *[document(value) for value in [True, -1, 1.2, "1", 2**64, float("nan"), float("inf")]],
        b'{"version":1,"created":1000,"entries":{"' + key.encode() + b'":NaN}}',
        b'{"version":1,"created":1000,"entries":{"' + key.encode() + b'":' + b"9" * 100 + b"}}",
        b"[" * 2000 + b"]" * 2000,
    ]
    for data in bad:
        valid.path.write_bytes(data)
        fresh = SnapshotCache(tmp_path, "context", clock=lambda: 1000.0)
        assert fresh.get(key) is None
        fresh.put(key, 0)
        assert SnapshotCache(tmp_path, "context", clock=lambda: 1000.0).get(key) == 0


@pytest.mark.parametrize("mutation", ["digit", "created", "order"])
def test_checksum_rejects_well_formed_corruption(tmp_path: Path, mutation: str) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    cache = SnapshotCache(tmp_path, "context", clock=lambda: 1000.0)
    key = cache.key("marginal", "one")
    other = cache.key("marginal", "two")
    cache.put(key, 100)
    cache.put(other, 200)
    original = cache.path.read_bytes()
    if mutation == "digit":
        changed = original.replace(f'"{key}":100'.encode(), f'"{key}":101'.encode())
    else:
        data = json.loads(original)
        if mutation == "created":
            data["created"] -= 1
        else:
            data["entries"] = dict(reversed(list(data["entries"].items())))
        changed = json.dumps(data).encode()
    assert changed != original
    cache.path.write_bytes(changed)
    assert SnapshotCache(tmp_path, "context", clock=lambda: 1000.0).get(key) is None


def test_snapshot_bounds_expiry_and_namespace(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from toolism.tokenizers import _cache

    now = [1000.0]
    monkeypatch.setattr(_cache, "MAX_ENTRIES", 2)
    cache = _cache.SnapshotCache(tmp_path, "context", clock=lambda: now[0])
    keys = [cache.key("marginal", n) for n in range(3)]
    assert all(keys)
    for i, key in enumerate(keys):
        assert key is not None
        cache.put(key, i)
    assert cache.get(keys[0]) is None
    assert cache.get(keys[1]) == 1
    other = _cache.SnapshotCache(tmp_path, "other", clock=lambda: now[0])
    assert other.get(other.key("marginal", 1)) is None
    original_created = json.loads(cache.path.read_bytes())["created"]
    now[0] += 100
    cache.put(keys[2], 3)
    assert json.loads(cache.path.read_bytes())["created"] == original_created
    stale = cache.generation
    now[0] += _cache.TTL_SECONDS
    cache.put(keys[0], 12, generation=stale)
    assert cache.get(keys[0]) is None
    cache.put(keys[0], 0)
    assert cache.get(keys[0]) == 0
    monkeypatch.setattr(_cache, "MAX_BYTES", 140)
    cache.put(keys[1], 1)
    assert cache.path.stat().st_size <= 140


@pytest.mark.parametrize("failure", ["fdopen", "fsync", "replace"])
def test_atomic_failure_preserves_previous_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    from toolism.tokenizers import _cache

    cache = _cache.SnapshotCache(tmp_path, "context")
    key = cache.key("marginal", 0)
    assert key is not None
    cache.put(key, 1)
    original = cache.path.read_bytes()

    def broken(*args: Any, **kwargs: Any) -> Any:
        raise OSError("disk failure")

    monkeypatch.setattr(_cache.os, failure, broken)
    cache.put(key, 2)
    assert cache.path.read_bytes() == original
    assert not list(tmp_path.glob(".toolism-counts-*.tmp"))


def test_concurrent_complete_snapshots_and_clear(tmp_path: Path) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    def write(value: int) -> None:
        cache = SnapshotCache(tmp_path, "context")
        key = cache.key("marginal", value)
        assert key is not None
        cache.put(key, value)

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(write, range(12)))
    data = json.loads((tmp_path / "toolism-token-counts-v1.json").read_bytes())
    assert data["entries"]
    assert set(data["entries"].values()) <= set(range(12))
    assert not list(tmp_path.glob(".toolism-counts-*.tmp"))
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("retain", encoding="utf-8")
    (tmp_path / "toolism-token-counts-v1.json").unlink()
    fresh = SnapshotCache(tmp_path, "context")
    assert fresh.get(fresh.key("marginal", 0)) is None
    assert unrelated.read_text(encoding="utf-8") == "retain"


def test_cache_has_no_payload_or_credentials(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path
) -> None:
    monkeypatch.setattr(
        CountingClient,
        "default_headers",
        {
            "anthropic-version": "2023-06-01",
            "X-Api-Key": "SECRET_CREDENTIAL_CANARY",
        },
    )
    counter, _ = tokenizer(monkeypatch)
    measure(TOOLSET, counter)
    data = (cache_directory / "toolism-token-counts-v1.json").read_bytes()
    for secret in [
        b"private-schema-canary",
        b"alpha",
        b"SECRET_CREDENTIAL_CANARY",
        b"input_schema",
    ]:
        assert secret not in data


def test_persistent_measure_in_a_fresh_process() -> None:
    code = (
        "import dataclasses,json,runpy,socket,sys; "
        "socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network')); "
        "m=runpy.run_path(sys.argv[1]); client=m['CountingClient'](); "
        "T=m['AnthropicTokenizer']; T._make_client=staticmethod(lambda:client); "
        "r=m['measure'](m['TOOLSET'],T('fresh-process')); "
        "print(json.dumps({'report':dataclasses.asdict(r),'calls':len(client.calls)}))"
    )

    def run() -> Any:
        return json.loads(
            subprocess.run(
                [sys.executable, "-B", "-c", code, str(Path(__file__))],
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True,
                timeout=30,
            ).stdout
        )

    cold, warm = run(), run()
    assert cold["calls"] == 5
    assert warm["report"] == cold["report"]
    assert warm["calls"] == 0


def test_request_crossing_expiry_recomputes_same_generation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from toolism.tokenizers import _cache

    now = [1000.0]
    monkeypatch.setattr(_cache, "time", lambda: now[0])
    counter, client = tokenizer(monkeypatch)
    counter.count_tool(TOOLS[0])
    changed = TOOLS[0].replace(description="changes across expiry")
    crossed = False

    def crosses(**kwargs: Any) -> Any:
        nonlocal crossed
        result = client.count(**kwargs)
        if now[0] > 1000:
            result.input_tokens += 20
        if not crossed:
            now[0] += _cache.TTL_SECONDS
            crossed = True
        return result

    client.messages.count_tokens = crosses
    expected = AnthropicTokenizer("m", client=CountingClient()).count_tool(changed)
    assert counter.count_tool(changed) == expected
    assert len(client.calls) == 5  # old raw, fresh probe and retried raw after original2
    fresh, no_calls = tokenizer(monkeypatch)
    assert fresh.count_tool(changed) == expected
    assert no_calls.calls == []


@pytest.mark.parametrize(("zero", "expected"), [(True, 0), (False, -10)])
def test_zero_raw_hits_and_negative_aggregate_are_preserved(
    monkeypatch: pytest.MonkeyPatch, zero: bool, expected: int
) -> None:
    first, client = tokenizer(monkeypatch)

    def special(**kwargs: Any) -> Any:
        client.calls.append(kwargs)
        return SimpleNamespace(input_tokens=0 if zero else 90 if kwargs.get("tools") else 100)

    client.messages.count_tokens = special
    assert first.count_tools(TOOLS) == expected
    second, warm = tokenizer(monkeypatch)
    assert second.count_tools(TOOLS) == expected
    assert warm.calls == []


@pytest.mark.parametrize(
    "url",
    ["https://u:password@example.test", "https://example.test?token=x", "https://example.test#x"],
)
def test_unsafe_endpoint_identity_disables_persistence(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path, url: str
) -> None:
    monkeypatch.setattr(CountingClient, "base_url", url)
    first, _ = tokenizer(monkeypatch)
    first.count_tool(TOOLS[0])
    second, client = tokenizer(monkeypatch)
    second.count_tool(TOOLS[0])
    assert len(client.calls) == 2
    assert not cache_directory.exists()


def test_missing_sdk_metadata_disables_persistence(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path
) -> None:
    def missing(_: str) -> str:
        raise anthropic_module.PackageNotFoundError("anthropic")

    monkeypatch.setattr(anthropic_module, "version", missing)
    counter, client = tokenizer(monkeypatch)
    counter.count_tool(TOOLS[0])
    assert len(client.calls) == 2
    assert not cache_directory.exists()


def test_unknown_home_is_a_cache_miss(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing_home(path: Path) -> Path:
        raise RuntimeError("Could not determine home directory")

    monkeypatch.setattr(Path, "expanduser", missing_home)
    counter, client = tokenizer(monkeypatch)
    assert counter.count_tool(TOOLS[0]) > 0
    assert len(client.calls) == 2


def test_ambiguous_headers_disable_persistence(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path
) -> None:
    monkeypatch.setattr(
        CountingClient,
        "default_headers",
        {
            "Anthropic-Version": "custom",
            "anthropic-version": "2023-06-01",
        },
    )
    counter, _ = tokenizer(monkeypatch)
    counter.count_tool(TOOLS[0])
    assert not cache_directory.exists()


def test_unusable_directory_is_a_miss(tmp_path: Path) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    directory = tmp_path / "not-a-directory"
    directory.write_text("retain", encoding="utf-8")
    cache = SnapshotCache(directory, "context")
    cache.put(cache.key("marginal", 0), 1)
    assert directory.read_text(encoding="utf-8") == "retain"
    assert cache.get(cache.key("marginal", 0)) == 1


def test_oversized_snapshot_is_ignored(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from toolism.tokenizers import _cache

    monkeypatch.setattr(_cache, "MAX_BYTES", 140)
    cache = _cache.SnapshotCache(tmp_path, "context")
    cache.path.write_bytes(b" " * 141)
    assert _cache.SnapshotCache(tmp_path, "context").get(cache.key("marginal", 0)) is None


def test_changed_slim_is_warm_without_requests(monkeypatch: pytest.MonkeyPatch) -> None:
    first, cold = tokenizer(monkeypatch)
    expected = slim(TOOLSET, level="annotations", tokenizer=first).report
    assert expected.before > expected.after
    assert cold.calls
    second, warm = tokenizer(monkeypatch)
    assert slim(TOOLSET, level="annotations", tokenizer=second).report == expected
    assert warm.calls == []


@pytest.mark.parametrize("mode", ["relative", "space", "tilde"])
def test_opt_in_path_forms(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, mode: str) -> None:
    monkeypatch.chdir(tmp_path)
    if mode == "relative":
        directory, actual = "relative-cache", tmp_path / "relative-cache"
    elif mode == "space":
        directory, actual = str(tmp_path / "with spaces"), tmp_path / "with spaces"
    else:
        monkeypatch.setenv("USERPROFILE", str(tmp_path))
        monkeypatch.setenv("HOME", str(tmp_path))
        directory, actual = "~/tilde-cache", tmp_path / "tilde-cache"
    monkeypatch.setenv("TOOLISM_CACHE_DIR", directory)
    counter, _ = tokenizer(monkeypatch)
    counter.count_tool(TOOLS[0])
    assert (actual / "toolism-token-counts-v1.json").is_file()


def test_relative_path_stays_anchored_after_chdir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("TOOLISM_CACHE_DIR", "relative-cache")
    counter, _ = tokenizer(monkeypatch)
    other = tmp_path / "other"
    other.mkdir()
    monkeypatch.chdir(other)
    counter.count_tool(TOOLS[0])
    assert (tmp_path / "relative-cache" / "toolism-token-counts-v1.json").is_file()
    assert not (other / "relative-cache").exists()


@pytest.mark.parametrize("stage", ["write", "flush"])
def test_stream_failure_preserves_previous_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, stage: str
) -> None:
    from toolism.tokenizers import _cache

    cache = _cache.SnapshotCache(tmp_path, "context")
    key = cache.key("marginal", 0)
    cache.put(key, 1)
    original = cache.path.read_bytes()
    fdopen = _cache.os.fdopen

    class FailingStream:
        def __init__(self, stream: Any) -> None:
            self.stream = stream

        def __enter__(self) -> Any:
            return self

        def __exit__(self, *args: Any) -> None:
            self.stream.close()

        def write(self, data: bytes) -> Any:
            if stage == "write":
                raise OSError("write failure")
            return self.stream.write(data)

        def flush(self) -> None:
            raise OSError("flush failure")

        def fileno(self) -> int:
            return self.stream.fileno()

    monkeypatch.setattr(_cache.os, "fdopen", lambda *a, **kw: FailingStream(fdopen(*a, **kw)))
    cache.put(key, 2)
    assert cache.path.read_bytes() == original
    assert not list(tmp_path.glob(".toolism-counts-*.tmp"))


def test_nonregular_target_is_ignored(tmp_path: Path) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    cache = SnapshotCache(tmp_path, "context")
    cache.path.mkdir()
    cache.put(cache.key("marginal", 0), 1)
    assert cache.path.is_dir()
    assert not list(tmp_path.glob(".toolism-counts-*.tmp"))


def test_concurrent_readers_see_complete_snapshots(tmp_path: Path) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    cache = SnapshotCache(tmp_path, "context")
    cache.put(cache.key("marginal", -1), 0)
    stop = threading.Event()
    ready = threading.Event()

    def read() -> int:
        samples = 0
        while not stop.is_set():
            try:
                data = json.loads(cache.path.read_bytes())
            except OSError:
                continue
            assert set(data) == {"version", "created", "entries", "checksum"}
            assert all(type(v) is int for v in data["entries"].values())
            samples += 1
            ready.set()
        return samples

    def write(i: int) -> None:
        fresh = SnapshotCache(tmp_path, "context")
        fresh.put(fresh.key("marginal", i), i)

    with ThreadPoolExecutor(max_workers=5) as pool:
        reader = pool.submit(read)
        assert ready.wait(timeout=5)
        try:
            list(pool.map(write, range(20)))
        finally:
            stop.set()
        assert reader.result(timeout=5) > 0


@pytest.mark.parametrize("operation", ["measure", "slim"])
def test_large_run_batches_writes_and_flushes_before_return(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    from toolism.tokenizers import _cache

    tools = ToolSet(
        tuple(
            ToolDef(f"tool{i}", input_schema={"type": "object", "title": "Drop"})
            for i in range(100)
        ),
        ToolFormat.MCP,
    )
    writes = 0
    original = _cache.SnapshotCache._write

    def write(cache: Any, encoded: bytes) -> Any:
        nonlocal writes
        writes += 1
        return original(cache, encoded)

    monkeypatch.setattr(_cache.SnapshotCache, "_write", write)

    def run(counter: AnthropicTokenizer) -> Any:
        if operation == "measure":
            return measure(tools, counter)
        return slim(tools, level="annotations", tokenizer=counter).report

    first, cold = tokenizer(monkeypatch)
    expected = run(first)
    assert len(cold.calls) == (103 if operation == "measure" else 204)
    assert 1 < writes <= 10
    published = writes
    second, warm = tokenizer(monkeypatch)
    assert run(second) == expected
    assert warm.calls == []
    assert writes == published


def test_batch_checkpoints_nested_scope_and_final_tail(tmp_path: Path) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    cache = SnapshotCache(tmp_path, "context")
    keys = [cache.key("marginal", i) for i in range(35)]
    assert hasattr(cache, "batch")
    with cache.batch():
        cache.put(keys[0], 0)
        assert SnapshotCache(tmp_path, "context").get(keys[0]) == 0
        with cache.batch():
            for i in range(1, 32):
                cache.put(keys[i], i)
        assert SnapshotCache(tmp_path, "context").get(keys[31]) is None
        cache.put(keys[32], 32)
        assert SnapshotCache(tmp_path, "context").get(keys[32]) == 32
        cache.put(keys[33], 33)
        assert SnapshotCache(tmp_path, "context").get(keys[33]) is None
    assert SnapshotCache(tmp_path, "context").get(keys[33]) == 33
    cache.put(keys[34], 34)
    assert SnapshotCache(tmp_path, "context").get(keys[34]) == 34


def test_batch_flushes_on_exception_and_retries_failed_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from toolism.tokenizers import _cache

    cache = _cache.SnapshotCache(tmp_path, "context")
    first, second = cache.key("marginal", 1), cache.key("marginal", 2)
    assert hasattr(cache, "batch")

    def run_then_fail(entries: list[tuple[str | None, int]]) -> None:
        with cache.batch():
            for key, value in entries:
                cache.put(key, value)
            raise RuntimeError("provider failed")

    with pytest.raises(RuntimeError, match="provider failed"):
        run_then_fail([(first, 1), (second, 2)])
    assert _cache.SnapshotCache(tmp_path, "context").get(second) == 2
    previous = cache.path.read_bytes()
    with monkeypatch.context() as broken:
        broken.setattr(_cache.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("disk")))
        with pytest.raises(RuntimeError, match="provider failed"):
            run_then_fail([(second, 3)])
    assert cache.path.read_bytes() == previous
    assert not list(tmp_path.glob(".toolism-counts-*.tmp"))
    with cache.batch():
        assert cache.get(second) == 3
    assert _cache.SnapshotCache(tmp_path, "context").get(second) == 3


@pytest.mark.parametrize("failure", ["fdopen", "fsync", "replace", "nonregular"])
def test_snapshot_write_reports_publication_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, failure: str
) -> None:
    from toolism.tokenizers import _cache

    cache = _cache.SnapshotCache(tmp_path, "context")
    encoded = cache._encode()
    assert cache._write(encoded) is True
    if failure == "nonregular":
        cache.path.unlink()
        cache.path.mkdir()
    else:
        monkeypatch.setattr(_cache.os, failure, lambda *a: (_ for _ in ()).throw(OSError("disk")))
    assert cache._write(encoded) is False
    assert not list(tmp_path.glob(".toolism-counts-*.tmp"))


@pytest.mark.parametrize("rollback", [False, True])
def test_batch_expiry_discards_pending_generation(tmp_path: Path, rollback: bool) -> None:
    from toolism.tokenizers import _cache

    now = [1000.0]
    cache = _cache.SnapshotCache(tmp_path, "context", clock=lambda: now[0])
    keys = [cache.key("marginal", i) for i in range(3)]
    assert hasattr(cache, "batch")
    with cache.batch():
        cache.put(keys[0], 0)
        cache.put(keys[1], 1)
        now[0] += -1 if rollback else _cache.TTL_SECONDS
    assert json.loads(cache.path.read_bytes())["entries"] == {keys[0]: 0}
    with cache.batch():
        cache.put(keys[2], 2)
        fresh = _cache.SnapshotCache(tmp_path, "context", clock=lambda: now[0])
        assert fresh.get(keys[2]) == 2
        assert fresh.get(keys[1]) is None


@pytest.mark.parametrize("before_replace", [False, True])
def test_abrupt_exit_preserves_checkpoint(tmp_path: Path, before_replace: bool) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    assert hasattr(SnapshotCache, "batch")
    code = """
import os, sys
from pathlib import Path
from toolism.tokenizers import _cache
cache = _cache.SnapshotCache(Path(sys.argv[1]), 'context')
with cache.batch():
    cache.put(cache.key('marginal', 0), 0)
    for i in range(1, 32):
        cache.put(cache.key('marginal', i), i)
    if sys.argv[2] == 'True':
        _cache.os.replace = lambda *a: os._exit(17)
        cache.put(cache.key('marginal', 32), 32)
    os._exit(17)
"""
    result = subprocess.run(
        [sys.executable, "-B", "-c", code, str(tmp_path), str(before_replace)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 17, result.stderr
    fresh = SnapshotCache(tmp_path, "context")
    assert fresh.get(fresh.key("marginal", 0)) == 0
    assert all(fresh.get(fresh.key("marginal", i)) is None for i in range(1, 33))


def test_failed_checkpoints_do_not_retry_every_put(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from toolism.tokenizers._cache import SnapshotCache

    attempts = 0

    def fail(*args: Any) -> bool:
        nonlocal attempts
        attempts += 1
        return False

    cache = SnapshotCache(tmp_path, "context")
    monkeypatch.setattr(cache, "_write", fail)
    assert hasattr(cache, "batch")
    with cache.batch():
        for i in range(100):
            cache.put(cache.key("marginal", i), i)
    assert 1 < attempts <= 10
    assert cache.get(cache.key("marginal", 99)) == 99


def test_provider_error_flushes_completed_counts(monkeypatch: pytest.MonkeyPatch) -> None:
    from toolism.errors import TokenizerUnavailable

    counter, client = tokenizer(monkeypatch)

    def fail_beta(**kwargs: Any) -> Any:
        tools = kwargs.get("tools", [])
        if len(tools) == 1 and tools[0]["name"] == "beta":
            raise RuntimeError("beta unavailable")
        return client.count(**kwargs)

    client.messages.count_tokens = fail_beta
    with pytest.raises(TokenizerUnavailable, match="beta unavailable"):
        measure(TOOLSET, counter)
    second, warm = tokenizer(monkeypatch)
    second.count_tool(TOOLS[0])
    assert warm.calls == []
    second.count_tool(TOOLS[1])
    assert len(warm.calls) == 1


@pytest.mark.parametrize("setting", ["unset", "empty", "injected", "custom-headers"])
def test_high_level_disabled_cache_has_no_side_effects(
    monkeypatch: pytest.MonkeyPatch, cache_directory: Path, setting: str
) -> None:
    if setting == "unset":
        monkeypatch.delenv("TOOLISM_CACHE_DIR")
    elif setting == "empty":
        monkeypatch.setenv("TOOLISM_CACHE_DIR", "")
    elif setting == "custom-headers":
        monkeypatch.setenv("ANTHROPIC_CUSTOM_HEADERS", "custom")

    def unexpected(_: str) -> str:
        pytest.fail("Disabled persistence must not look up SDK metadata")

    monkeypatch.setattr(anthropic_module, "version", unexpected)
    if setting == "injected":
        counter = AnthropicTokenizer("m", client=CountingClient())
    else:
        counter, _ = tokenizer(monkeypatch)
    assert measure(TOOLSET, counter).total > 0
    assert slim(TOOLSET, level="annotations", tokenizer=counter).report.before > 0
    assert not cache_directory.exists()


def test_custom_tokenizer_needs_no_batch_method() -> None:
    class CustomTokenizer:
        name = "custom"
        local = False
        approximate = False

        def count_tool(self, tool: ToolDef) -> int:
            return 1

        def count_tools(self, tools: Any) -> int:
            return 123

    counter = CustomTokenizer()
    assert measure(TOOLSET, counter).total == 123
    report = slim(TOOLSET, rules=[], tokenizer=counter).report
    assert report.before == report.after == 123


def test_batch_preserves_bounds_and_rejects_invalid_updates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from toolism.tokenizers import _cache

    monkeypatch.setattr(_cache, "MAX_ENTRIES", 2)
    cache = _cache.SnapshotCache(tmp_path, "context")
    assert hasattr(cache, "batch")
    with cache.batch():
        cache.put(None, 1)
        cache.put("invalid", 1)
        cache.put(cache.key("marginal", 0), -1)
    assert not cache.path.exists()
    with cache.batch():
        for i in range(35):
            cache.put(cache.key("marginal", i), i)
            assert len(cache._entries) <= 2
    fresh = _cache.SnapshotCache(tmp_path, "context")
    assert fresh.get(fresh.key("marginal", 32)) is None
    assert fresh.get(fresh.key("marginal", 33)) == 33
    assert fresh.get(fresh.key("marginal", 34)) == 34


@pytest.mark.parametrize("rollback", [False, True])
def test_expiry_during_checkpoint_preserves_first_fresh_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rollback: bool
) -> None:
    from toolism.tokenizers import _cache

    now = [1000.0]
    cache = _cache.SnapshotCache(tmp_path, "context", clock=lambda: now[0])
    old, fresh = cache.key("marginal", "old"), cache.key("marginal", "fresh")
    original_encode = cache._encode
    crossed = False

    def encode() -> bytes:
        nonlocal crossed
        encoded = original_encode()
        if not crossed:
            crossed = True
            now[0] += -1 if rollback else _cache.TTL_SECONDS
        return encoded

    monkeypatch.setattr(cache, "_encode", encode)
    with cache.batch():
        cache.put(old, 1)
        assert not cache.path.exists()
        cache.put(fresh, 2)
        restored = _cache.SnapshotCache(tmp_path, "context", clock=lambda: now[0])
        assert restored.get(fresh) == 2
        assert restored.get(old) is None
