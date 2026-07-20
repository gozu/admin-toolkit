"""Regression: the Agent Tuning save path must never overwrite version history
with an empty frame just because the read failed transiently.

Before the fix, save did `rows = _read_rows(force=True) + [row]` and `_read_rows`
swallowed every exception (returning []), so a dataset-read hiccup would write
back only the new row — wiping all prior versions with no error surfaced.
`_read_rows_for_save` raises on read failure instead.
"""

import conftest  # noqa: F401  (installs the python-lib path + DSS stubs)

import pytest

from adk_backend.routes import agent_tuning as at


class _Schema:
    def __init__(self, cols):
        self._cols = cols

    def get_schema(self):
        return {'columns': [{'name': c} for c in self._cols]}


class _FakeProject:
    def __init__(self, has_dataset=True, list_raises=False, schema_cols=('saved_at',)):
        self.has_dataset = has_dataset
        self.list_raises = list_raises
        self.schema_cols = schema_cols
        self.created = False

    def list_datasets(self):
        if self.list_raises:
            raise RuntimeError('DSS API transiently unreachable')
        return [{'name': at.DATASET_NAME}] if self.has_dataset else []

    def get_dataset(self, name):
        return _Schema(self.schema_cols)

    def new_managed_dataset(self, name):
        project = self

        class _Builder:
            def with_store_into(self, conn):
                return self

            def create(self_inner):
                project.created = True
        return _Builder()


def test_save_read_raises_on_listing_failure(monkeypatch):
    """A transient list_datasets failure must propagate (abort the save), not
    silently return [] and let write_with_schema wipe the dataset."""
    project = _FakeProject(list_raises=True)
    with pytest.raises(RuntimeError):
        at._read_rows_for_save(project)


def test_save_read_raises_on_dataframe_failure(monkeypatch):
    """Dataset exists with a real schema but the dataframe read throws — must
    raise, never hand back an empty frame the caller would persist."""
    project = _FakeProject(has_dataset=True, schema_cols=('saved_at', 'note'))

    class _BoomDataset:
        def get_dataframe(self, **kwargs):
            raise RuntimeError('read blew up mid-save')

    monkeypatch.setattr(at.dataiku, 'Dataset', lambda name: _BoomDataset(), raising=False)
    with pytest.raises(RuntimeError):
        at._read_rows_for_save(project)


def test_save_read_empty_when_dataset_absent(monkeypatch):
    """First-ever save: no dataset yet → create it and return [] (legit empty)."""
    project = _FakeProject(has_dataset=False)
    monkeypatch.setattr(at, '_connection', lambda: 'filesystem_managed')
    assert at._read_rows_for_save(project) == []
    assert project.created is True


def test_save_read_empty_when_schema_unwritten(monkeypatch):
    """Dataset exists but was never written (no schema columns) → [] without
    ever calling get_dataframe (which would fail on a schemaless dataset)."""
    project = _FakeProject(has_dataset=True, schema_cols=())

    def _should_not_read(name):
        raise AssertionError('get_dataframe must not run on a schemaless dataset')

    monkeypatch.setattr(at.dataiku, 'Dataset', _should_not_read, raising=False)
    assert at._read_rows_for_save(project) == []
