from __future__ import annotations

from jmlogviewer.core.marks import Mark, MarkStore


def test_mark_normalises_its_range() -> None:
    mark = Mark(9, 3)
    assert (mark.first, mark.last) == (3, 9)
    assert 5 in mark
    assert 10 not in mark
    assert mark.size == 7


def test_describe_lines_is_one_based() -> None:
    assert Mark(0, 0).describe_lines() == "1"
    assert Mark(1199, 1299).describe_lines() == "1,200\N{EN DASH}1,300"


def test_toggle() -> None:
    store = MarkStore()
    added = store.toggle(4)
    assert added is not None
    assert store.at(4) is added
    assert store.toggle(4) is None
    assert not store


def test_toggle_inside_a_section_removes_it() -> None:
    store = MarkStore()
    store.add(10, 20)
    assert store.toggle(15) is None
    assert len(store) == 0


def test_at_prefers_the_innermost_mark() -> None:
    store = MarkStore()
    outer = store.add(0, 100)
    inner = store.add(40, 50)
    assert store.at(45) is inner
    assert store.at(60) is outer
    assert store.at(101) is None


def test_marks_stay_sorted_and_navigable() -> None:
    store = MarkStore()
    third = store.add(30, 30)
    first = store.add(10, 12)
    second = store.add(20, 25)
    assert list(store) == [first, second, third]
    assert store.next_after(10) is second
    assert store.next_after(30) is None
    assert store.prev_before(20) is first
    assert store.prev_before(10) is None
    assert store.overlapping(11, 21) == [first, second]


def test_update_keeps_order() -> None:
    store = MarkStore()
    a = store.add(1, 1)
    b = store.add(5, 5)
    store.update(a, first=9, last=7)
    assert (a.first, a.last) == (7, 9)
    assert list(store) == [b, a]


def test_serialisation_round_trip() -> None:
    mark = Mark(3, 8, color="#123456", label="Startup")
    copy = Mark.from_dict(mark.to_dict())
    assert (copy.first, copy.last, copy.color, copy.label) == (3, 8, "#123456", "Startup")
    assert copy.id != mark.id
