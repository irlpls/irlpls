"""irlpls/events.py: append, hash chain, tamper detection, concurrency."""
import threading

from irlpls import events


def test_append_and_verify(tmp_path):
    p = tmp_path / "registry.ndjson"
    assert events.verify_chain(p) == (True, [])
    a = events.append(p, "submitted", domain="a.example", card_id="x", ts=1)
    b = events.append(p, "accepted", domain="a.example", card_id="x", ts=2)
    assert a["prev_hash"] == "0" * 64 and b["prev_hash"] == a["hash"]
    assert events.verify_chain(p) == (True, [])
    assert [e["action"] for e in events.read(p)] == ["submitted", "accepted"]


def test_unknown_action(tmp_path):
    import pytest
    with pytest.raises(events.EventError):
        events.append(tmp_path / "l", "published")


def _log(tmp_path):
    p = tmp_path / "l.ndjson"
    for i, a in enumerate(["submitted", "crawled", "gated", "rejected"]):
        events.append(p, a, card_id=f"c{i}", ts=i)
    return p


def test_edit_detected(tmp_path):
    p = _log(tmp_path)
    p.write_text(p.read_text().replace('"c1"', '"cX"'))
    ok, probs = events.verify_chain(p)
    assert not ok and probs[0]["line"] == 2


def test_delete_and_reorder_detected(tmp_path):
    p = _log(tmp_path)
    lines = p.read_text().splitlines()
    p.write_text("\n".join(lines[:1] + lines[2:]) + "\n")
    assert not events.verify_chain(p)[0]
    p.write_text("\n".join([lines[1], lines[0]] + lines[2:]) + "\n")
    assert not events.verify_chain(p)[0]


def test_rewritten_hash_still_breaks_next_link(tmp_path):
    p = _log(tmp_path)
    ls = p.read_text().splitlines()
    import json
    e = json.loads(ls[1])
    e["card_id"] = "evil"
    e["hash"] = events.line_hash(e)
    ls[1] = events._canon(e)
    p.write_text("\n".join(ls) + "\n")
    ok, probs = events.verify_chain(p)
    assert not ok and probs[0]["line"] == 3


def test_concurrent_appends_keep_chain(tmp_path):
    p = tmp_path / "l.ndjson"
    ts = [threading.Thread(target=lambda i=i: [events.append(p, "crawled", card_id=f"{i}-{j}") for j in range(10)]) for i in range(5)]
    [t.start() for t in ts]
    [t.join() for t in ts]
    assert len(events.read(p)) == 50 and events.verify_chain(p)[0]
