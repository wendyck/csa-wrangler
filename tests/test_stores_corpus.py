"""Tests for corpus write safety: ETag-cached reads, If-Match writes (stores.py) and the
scripts/corpus_sync.py pull/push helper."""
import importlib
import io
import itertools
import json
from pathlib import Path

import pytest
from botocore.exceptions import ClientError

from planner import config, stores

KEY = "corpus/recipes_tagged.json"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"


def _error(code, status):
    return ClientError({"Error": {"Code": code, "Message": code},
                        "ResponseMetadata": {"HTTPStatusCode": status}}, "op")


class FakeS3:
    """In-memory S3 that honours the conditional-request headers we use."""

    def __init__(self, objects=None):
        self._etags = (f'"etag-{n}"' for n in itertools.count(1))
        self.objects = {}            # key -> (body bytes, etag)
        self.full_reads = 0
        for key, body in (objects or {}).items():
            self.write(key, body)

    def write(self, key, body):
        """An out-of-band change (another writer)."""
        self.objects[key] = (body if isinstance(body, bytes) else json.dumps(body).encode(),
                             next(self._etags))

    def get_object(self, Bucket, Key, IfNoneMatch=None):
        if Key not in self.objects:
            raise _error("NoSuchKey", 404)
        body, etag = self.objects[Key]
        if IfNoneMatch == etag:
            raise _error("304", 304)   # what botocore raises for Not Modified
        self.full_reads += 1
        return {"Body": io.BytesIO(body), "ETag": etag}

    def put_object(self, Bucket, Key, Body, ContentType=None, IfMatch=None, IfNoneMatch=None):
        current = self.objects.get(Key)
        if IfMatch is not None and (current is None or current[1] != IfMatch):
            raise _error("PreconditionFailed", 412)
        if IfNoneMatch == "*" and current is not None:
            raise _error("PreconditionFailed", 412)
        self.objects[Key] = (Body, next(self._etags))
        return {"ETag": self.objects[Key][1]}


@pytest.fixture
def s3(monkeypatch):
    fake = FakeS3({KEY: [{"title": "A"}]})
    monkeypatch.setattr(stores, "_client", lambda name: fake)
    monkeypatch.setattr(stores, "_corpus_cache", None)
    monkeypatch.setattr(config, "BUCKET", "bucket")
    monkeypatch.setenv("CORPUS_S3_KEY", KEY)   # keep config.get off SSM
    return fake


# ---- stores ----

def test_load_corpus_rereads_only_when_etag_changes(s3):
    assert stores.load_corpus() == [{"title": "A"}]
    assert stores.load_corpus() == [{"title": "A"}]
    assert s3.full_reads == 1                      # second call was a 304

    s3.write(KEY, [{"title": "A"}, {"title": "B"}])
    assert len(stores.load_corpus()) == 2          # a warm Lambda sees the new recipe
    assert s3.full_reads == 2


def test_load_corpus_propagates_other_errors(s3):
    del s3.objects[KEY]
    with pytest.raises(ClientError):
        stores.load_corpus()


def test_put_corpus_with_current_etag(s3):
    corpus, etag = stores.load_corpus_for_update()
    corpus.append({"title": "B"})
    stores.put_corpus(corpus, etag)
    assert json.loads(s3.objects[KEY][0]) == [{"title": "A"}, {"title": "B"}]


def test_put_corpus_with_stale_etag_raises_corpus_changed(s3):
    corpus, etag = stores.load_corpus_for_update()
    s3.write(KEY, [{"title": "A"}, {"title": "saved from browser"}])
    with pytest.raises(stores.CorpusChanged):
        stores.put_corpus(corpus + [{"title": "B"}], etag)
    assert json.loads(s3.objects[KEY][0])[1]["title"] == "saved from browser"   # untouched


def test_put_corpus_maps_conflict_and_reraises_others(s3, monkeypatch):
    def put(**kw):
        raise _error("ConditionalRequestConflict", 409)
    monkeypatch.setattr(s3, "put_object", put)
    with pytest.raises(stores.CorpusChanged):
        stores.put_corpus([], '"x"')

    def put_denied(**kw):
        raise _error("AccessDenied", 403)
    monkeypatch.setattr(s3, "put_object", put_denied)
    with pytest.raises(ClientError):
        stores.put_corpus([], '"x"')


# ---- scripts/corpus_sync.py ----

@pytest.fixture
def sync(monkeypatch, s3):
    monkeypatch.syspath_prepend(str(SCRIPTS))
    mod = importlib.import_module("corpus_sync")
    monkeypatch.setattr(mod, "_s3", lambda: s3)
    return mod


def _run(sync, tmp_path, cmd):
    sync.main([cmd, "--bucket", "bucket", "--corpus", str(tmp_path / "recipes_tagged.json")])


def test_pull_then_push_round_trip(sync, s3, tmp_path):
    _run(sync, tmp_path, "pull")
    local = tmp_path / "recipes_tagged.json"
    assert json.loads(local.read_text()) == [{"title": "A"}]
    assert (tmp_path / ".recipes_tagged.etag").read_text() == s3.objects[KEY][1]

    local.write_text(json.dumps([{"title": "A"}, {"title": "B"}]))
    _run(sync, tmp_path, "push")
    assert len(json.loads(s3.objects[KEY][0])) == 2
    assert (tmp_path / ".recipes_tagged.etag").read_text() == s3.objects[KEY][1]

    local.write_text(json.dumps([{"title": "A"}, {"title": "B"}, {"title": "C"}]))
    _run(sync, tmp_path, "push")                   # saved ETag lets you push again
    assert len(json.loads(s3.objects[KEY][0])) == 3


def test_push_refuses_when_s3_changed_since_pull(sync, s3, tmp_path):
    _run(sync, tmp_path, "pull")
    s3.write(KEY, [{"title": "A"}, {"title": "saved from browser"}])
    (tmp_path / "recipes_tagged.json").write_text(json.dumps([{"title": "local edit"}]))
    with pytest.raises(SystemExit, match="S3 changed since your last pull"):
        _run(sync, tmp_path, "push")
    assert json.loads(s3.objects[KEY][0])[1]["title"] == "saved from browser"


def test_push_without_etag_file_refuses_when_corpus_exists(sync, s3, tmp_path):
    (tmp_path / "recipes_tagged.json").write_text("[]")
    with pytest.raises(SystemExit, match="run pull first"):
        _run(sync, tmp_path, "push")
    assert json.loads(s3.objects[KEY][0]) == [{"title": "A"}]


def test_push_without_etag_file_creates_first_corpus(sync, s3, tmp_path):
    del s3.objects[KEY]
    (tmp_path / "recipes_tagged.json").write_text(json.dumps([{"title": "first"}]))
    _run(sync, tmp_path, "push")
    assert json.loads(s3.objects[KEY][0]) == [{"title": "first"}]


@pytest.mark.parametrize("content", ['{"not": "a list"}', "[{broken"])
def test_push_refuses_a_file_that_is_not_a_corpus(sync, s3, tmp_path, content):
    _run(sync, tmp_path, "pull")
    (tmp_path / "recipes_tagged.json").write_text(content)
    with pytest.raises((SystemExit, ValueError)):
        _run(sync, tmp_path, "push")
    assert json.loads(s3.objects[KEY][0]) == [{"title": "A"}]


def test_bucket_is_required(sync, tmp_path, monkeypatch):
    monkeypatch.delenv("CSA_BUCKET", raising=False)
    with pytest.raises(SystemExit):
        sync.main(["pull", "--corpus", str(tmp_path / "c.json")])
