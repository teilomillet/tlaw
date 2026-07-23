"""Laws for `tlaw.record`: the composite input domain.

Born from the anef probe (issue #1, gap G1): the flagship parity law needs
a whole verification request — a nested composite of documents, sources,
and field selections — and with only scalar and list domains it could not
be written generatively at all.

Domains used as annotations are module-level names here for the reason the
documentation gives: postponed string annotations are resolved against the
law module's globals.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated

import pytest
from hypothesis import given, settings

import tlaw


@dataclass(frozen=True)
class Point:
    name: str
    size: int


Shape = tlaw.record(name=tlaw.one_of(["a", "b"]), size=tlaw.integer(1, 3))
Points = tlaw.record(Point, name=tlaw.one_of(["a"]), size=tlaw.integer(1, 3))
Source = tlaw.record(attachment_id=tlaw.one_of(["upload-1"]))
Document = tlaw.record(source=Source, size=tlaw.integer(0, 2))
Request = tlaw.record(documents=tlaw.list_of(Document, min_size=1, max_size=2))
Counted = tlaw.record(count=int)


def test_a_record_without_a_constructor_generates_a_dictionary() -> None:
    @tlaw.law
    def every_shape_has_both_declared_fields(shape: Shape):
        return set(shape) == {"name", "size"} and shape["name"] in ("a", "b")

    every_shape_has_both_declared_fields()


def test_a_constructor_is_applied_to_the_drawn_fields_by_keyword() -> None:
    @tlaw.law
    def every_point_is_a_point(point: Points):
        return isinstance(point, Point) and 1 <= point.size <= 3

    every_point_is_a_point()


def test_records_nest_through_each_other_and_through_list_of() -> None:
    @tlaw.law
    def every_nested_document_keeps_its_source(request: Request):
        return all(
            document["source"]["attachment_id"] == "upload-1"
            for document in request["documents"]
        )

    every_nested_document_keeps_its_source()


def test_the_generated_shape_stays_visible_in_the_description() -> None:
    """A composite domain must be readable where the law is read."""
    assert repr(Source) == "record(attachment_id=one_of('upload-1'))"
    assert repr(Points) == "record(Point, name=one_of('a'), size=integer(1, 3))"
    assert repr(tlaw.list_of(Source, min_size=1)) == (
        "list_of(record(attachment_id=one_of('upload-1')), min_size=1)"
    )
    assert repr(Request) == (
        "record(documents=list_of("
        "record(source=record(attachment_id=one_of('upload-1')), "
        "size=integer(0, 2)), min_size=1, max_size=2))"
    )


def test_a_plain_type_field_generates_what_the_bare_annotation_would() -> None:
    @tlaw.law
    def every_count_is_an_integer(counted: Counted):
        return isinstance(counted["count"], int)

    every_count_is_an_integer()


def test_a_record_with_no_fields_is_refused_as_stating_nothing() -> None:
    with pytest.raises(ValueError):
        tlaw.record()


def test_a_non_callable_constructor_is_refused() -> None:
    with pytest.raises(TypeError):
        tlaw.record("Point", name=tlaw.one_of(["a"]))


def test_a_field_that_is_neither_a_domain_nor_a_type_is_refused() -> None:
    """A constant must be stated as one_of([value]), never guessed."""
    with pytest.raises(TypeError) as refusal:
        tlaw.record(schema_version="verification.request.v2")

    assert "one_of([value])" in str(refusal.value)


def test_an_annotated_base_that_contradicts_the_record_is_refused() -> None:
    """The visible contract cannot claim a type the record never builds."""
    with pytest.raises(TypeError):

        @tlaw.law
        def points_are_strings(point: Annotated[str, Points]):
            return True


def test_an_annotated_record_generates_through_a_checker_clean_alias() -> None:
    @tlaw.law
    def every_annotated_point_is_a_point(point: Annotated[Point, Points]):
        return isinstance(point, Point)

    every_annotated_point_is_a_point()


def test_a_record_law_fails_with_the_generated_composite_retained() -> None:
    @tlaw.law
    def every_shape_is_named_a(shape: Shape):
        return tlaw.same(shape["name"], "a")

    with pytest.raises(AssertionError) as failure:
        every_shape_is_named_a()

    assert "first difference" in str(failure.value)


# A record's depends() names sibling fields, and only those. The two scopes
# share one word, so the boundary between them is enforced, never inferred.

Selections = tlaw.one_of([("a",), ("b",), ("a", "b")])
Related = tlaw.record(
    fields=Selections,
    chosen=tlaw.depends(lambda fields: tlaw.one_of(fields)),
)


def test_a_record_field_may_depend_on_a_sibling_field() -> None:
    @tlaw.law
    def the_chosen_field_is_always_one_that_was_selected(related: Related):
        return related["chosen"] in related["fields"]

    the_chosen_field_is_always_one_that_was_selected()


def test_the_sibling_relation_holds_by_construction_not_by_filtering() -> None:
    """Nothing is discarded, so a law using the record cannot go vacuous.

    A dependency narrow enough to be hostile to filtering — one choice out
    of the sibling's drawn tuple — still generates a full example budget.
    """
    drawn: set[tuple[object, object]] = set()

    @given(Related.strategy)
    @settings(database=None, derandomize=True, max_examples=200)
    def collect(value: dict[str, object]) -> None:
        drawn.add((value["fields"], value["chosen"]))

    collect()

    # Every pair the relation permits is reachable, and no pair it forbids
    # was ever produced. Filtering would show up as the missing pairs of a
    # narrow dependency, or as discarded examples.
    assert drawn == {
        (("a",), "a"),
        (("b",), "b"),
        (("a", "b"), "a"),
        (("a", "b"), "b"),
    }


def test_a_records_depends_cannot_reach_a_law_parameter() -> None:
    """One word must not silently mean two different namespaces.

    `budget` is a real law parameter elsewhere in this suite. Resolving it
    from inside a record would be a guess about which contract was meant, so
    it is refused when the record is defined — before any law exists.
    """
    with pytest.raises(TypeError) as refusal:
        tlaw.record(size=tlaw.depends(lambda budget: tlaw.integer(1, budget)))

    message = str(refusal.value)
    assert "not a field of this record" in message
    assert "sibling fields only" in message
    assert "cannot reach a law parameter" in message


def test_a_cycle_between_record_fields_is_refused_as_a_record_not_a_law() -> None:
    with pytest.raises(TypeError) as refusal:
        tlaw.record(
            a=tlaw.depends(lambda b: tlaw.one_of([1])),
            b=tlaw.depends(lambda a: tlaw.one_of([1])),
        )

    assert str(refusal.value).startswith("record ")
    assert "cycle" in str(refusal.value)


def test_a_dependent_field_stays_visible_in_the_description() -> None:
    assert repr(Related) == (
        "record(fields=one_of(('a',), ('b',), ('a', 'b')), chosen=depends(fields))"
    )
