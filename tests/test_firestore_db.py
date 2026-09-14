"""Unit tests for Firestore persistence adapter."""

import pytest
from unittest.mock import MagicMock
from dojo.firestore_db import FirestoreDojoDatabase


class MockDocRef:
    def __init__(self, doc_id="test_id", exists=False, data=None):
        self.id = doc_id
        self._exists = exists
        self._data = data or {}

    def get(self):
        mock_snap = MagicMock()
        mock_snap.exists = self._exists
        mock_snap.to_dict.return_value = self._data
        return mock_snap

    def set(self, data, merge=False):
        self._data.update(data)
        self._exists = True

    def to_dict(self):
        return self._data


class MockCollection:
    def __init__(self, name):
        self.name = name
        self.docs = {}

    def document(self, doc_id=None):
        if not doc_id:
            doc_id = f"auto_{len(self.docs)+1}"
        if doc_id not in self.docs:
            self.docs[doc_id] = MockDocRef(doc_id)
        return self.docs[doc_id]

    def stream(self):
        for doc in self.docs.values():
            snap = MagicMock()
            snap.id = doc.id
            snap.to_dict.return_value = doc._data
            yield snap

    def where(self, field, op, val):
        mock_query = MagicMock()
        filtered = []
        for doc in self.docs.values():
            if val is None and doc._data.get(field) is None:
                snap = MagicMock()
                snap.id = doc.id
                snap.to_dict.return_value = doc._data
                filtered.append(snap)
            elif doc._data.get(field) == val:
                snap = MagicMock()
                snap.id = doc.id
                snap.to_dict.return_value = doc._data
                filtered.append(snap)
        mock_query.stream.return_value = iter(filtered)
        return mock_query


class MockFirestoreClient:
    def __init__(self):
        self.collections = {}

    def collection(self, name):
        if name not in self.collections:
            self.collections[name] = MockCollection(name)
        return self.collections[name]

    def batch(self):
        mock_batch = MagicMock()
        return mock_batch


def test_firestore_upsert_and_retrieve():
    client = MockFirestoreClient()
    fdb = FirestoreDojoDatabase(project="test-project", client=client)

    # Upsert feed item
    items = [{
        "id": "item_123",
        "header": "Gym Tomorrow",
        "content_text": "Bring sneakers for PE",
        "senderName": "Ms. Alonso",
        "item_timestamp": "2026-09-14T10:00:00Z"
    }]
    new_c, upd_c = fdb.upsert_feed_items(items)
    assert new_c == 1
    assert upd_c == 0

    # Retrieve all
    feeds = fdb.get_all_feed_items()
    assert len(feeds) == 1
    assert feeds[0]["id"] == "item_123"
    assert "Ms. Alonso" in feeds[0]["author_name"]

    # Retrieve undigested
    undigested = fdb.get_undigested_items()
    assert len(undigested["feed_items"]) == 1

    # Session storage
    fdb.save_session({"cookie_a": "val_a"})
    session = fdb.load_session()
    assert session == {"cookie_a": "val_a"}

    # Stats
    stats = fdb.get_stats()
    assert stats["feed_total"] == 1
    assert stats["backend"] == "firestore"
