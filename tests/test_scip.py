"""SCIP import: a tiny protobuf encoder builds an index the way scip-python would, for the Python fixture."""

import sqlite3

from code_atlas import scip
from code_atlas.scan import scan
from test_scan import REPO, write


def _varint(n: int) -> bytes:
    out = bytearray()
    while True:
        b = n & 0x7F
        n >>= 7
        out.append(b | (0x80 if n else 0))
        if not n:
            return bytes(out)


def _field(num: int, value) -> bytes:
    if isinstance(value, int):
        return _varint(num << 3) + _varint(value)
    data = value.encode() if isinstance(value, str) else value
    return _varint(num << 3 | 2) + _varint(len(data)) + data


def _occ(line: int, char: int, end: int, symbol: str, roles: int = 0, typed: bool = False) -> bytes:
    if typed:  # newer producers: SingleLineRange (field 8); line 0 is omitted on the wire like proto3 does
        rng = _field(8, (_field(1, line) if line else b"") + _field(2, char) + _field(3, end))
    else:
        rng = _field(1, b"".join(_varint(x) for x in (line, char, end)))
    return rng + _field(2, symbol) + (_field(3, roles) if roles else b"")


def _doc(path: str, occs: list[bytes]) -> bytes:
    return _field(1, path) + b"".join(_field(2, o) for o in occs) + _field(4, "python")


SYM = "scip-python python app 0.1 "
GREET = SYM + "`app.models`/User#greet()."
USER = SYM + "`app.models`/User#"
MAKE = SYM + "`app.service`/make_user()."


def build_index(path):
    models = _doc("app/models.py", [_occ(9, 6, 10, USER, 1), _occ(13, 8, 13, GREET, 1)])  # 0-based lines
    service = _doc("app/service.py", [
        _occ(4, 4, 13, MAKE, 1, typed=True),
        _occ(5, 8, 12, USER, typed=True),     # u = User()
        _occ(7, 13, 18, GREET),               # return u.greet()   ← the heuristic can't type `u`
        _occ(3, 0, 5, SYM + "local 0"),
        _occ(0, 0, 4, SYM + "`app`/", typed=True),  # line 0 in typed form: field omitted
    ])
    data = _field(1, b"") + _field(2, models) + _field(2, service)
    path.write_bytes(data)


def test_decoder_and_symbol_names(tmp_path):
    f = tmp_path / "index.scip"
    build_index(f)
    docs = scip.load(f)
    assert [d.path for d in docs] == ["app/models.py", "app/service.py"]
    assert all(not o.symbol.startswith("local") for d in docs for o in d.occurrences)
    assert docs[1].occurrences[2].line == 7
    assert docs[1].occurrences[0].line == 4  # typed SingleLineRange
    assert docs[1].occurrences[-1].line == 0  # typed range with the line field omitted
    assert scip.symbol_name(GREET) == "greet"
    assert scip.symbol_name(USER) == "User"


def test_scan_with_scip_replaces_symbol_edges(tmp_path):
    write(tmp_path, REPO)
    idx = tmp_path / "out" / "index.scip"
    idx.parent.mkdir()
    build_index(idx)
    rep = scan(tmp_path, scip=[idx])
    assert rep.stats["scip"]["covered_files"] == 2
    db = sqlite3.connect(tmp_path / ".codemap" / "cache" / "index.sqlite")
    edges = {(s, d, t, v) for s, d, t, v in db.execute("select src, dst, type, via from edges")}
    assert ("app/service.py#make_user", "app/models.py#User.greet", "calls", "scip") in edges
    # the heuristic edge to util.log came from a covered file and is gone; imports stay
    assert not any(s.startswith("app/service.py#") and v != "scip" and t != "imports" for s, _, t, v in edges)
    assert any(s == "app/service.py" and t == "imports" for s, _, t, _ in edges)
    # files the index does not cover keep their heuristic edges
    assert any(s.startswith("main.py") and v != "scip" for s, _, _, v in edges)
    # the index is remembered for the next scan
    scan(tmp_path)
    edges2 = {(s, d, t, v) for s, d, t, v in db.execute("select src, dst, type, via from edges")}
    assert ("app/service.py#make_user", "app/models.py#User.greet", "calls", "scip") in edges2


def test_real_indexer_windows_absolute_paths_are_normalized(tmp_path):
    write(tmp_path, REPO)
    data = _field(2, _doc(str(tmp_path / "app/models.py"), [_occ(9,6,10,USER,1),_occ(13,8,13,GREET,1)]))
    data += _field(2, _doc(str(tmp_path / "app/service.py"), [_occ(4,4,13,MAKE,1),_occ(7,13,18,GREET)]))
    path = tmp_path / "index.scip"
    path.write_bytes(data)
    report = scan(tmp_path, scip=[path])
    assert report.stats["scip"]["covered_files"] == 2
