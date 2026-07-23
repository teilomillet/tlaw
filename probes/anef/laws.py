"""anef probe laws: the verification-compilation refactor as a tlaw driver.

Issue #1. anef is read, never modified: these laws import its installed
functions and run under its own environment.

What is under law
-----------------
The refactor extracts the `interpret -> resolve` block out of
`backend.verification.compose.compose` into a new `compile_verification`.
That function does not exist yet, so `compiled(...)` below is that block as
`compose` writes it today, calling anef's real `interpret`, `resolve`, and
anef's real `_CALLER_RESOLVE_CODES` attribution set. Nothing about anef's
behavior is reimplemented here; only the orchestration is mirrored, and it
is mirrored so these laws can be repointed at `compile_verification`
without changing a single claim. That is the parity the refactor needs.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

import tlaw

from backend.json import JsonArray, JsonMember, JsonNumber, JsonObject
from backend.result import Err, Ok, Result
from backend.submission import AttachmentId
from backend.verification.compose import _CALLER_RESOLVE_CODES
from backend.verification.field import (
    FieldId,
    FieldKind,
    FieldQuestion,
    FieldSpec,
    catalog,
)
from backend.verification.intake import (
    ClaimSource,
    ClaimValue,
    DeclaredFields,
    DocumentId,
    DocumentRequest,
    DocumentTypeId,
    Limits as IntakeLimits,
    LocalSource,
    SourceId,
    VerificationId,
    VerificationRequest,
    VerificationTypeId,
    interpret,
)
from backend.verification.profile import (
    DOCUMENT_SOURCE,
    Capabilities,
    CheckId,
    FieldRef,
    Limits as ProfileLimits,
    MatchCheck,
    ProfileId,
    ProfileKey,
    ProfileRef,
    ProfileVersion,
    ResolveFailure,
    VerificationProfile,
    resolve,
)


# The deployment under law ---------------------------------------------------

_FIELDS = (
    FieldSpec(
        FieldId("person_name"),
        FieldKind("text"),
        FieldQuestion("Transcribe the person's full name as printed."),
    ),
    FieldSpec(
        FieldId("birth_date"),
        FieldKind("text"),
        FieldQuestion("Transcribe the person's date of birth as printed."),
    ),
    FieldSpec(
        FieldId("academic_year"),
        FieldKind("text"),
        FieldQuestion("Transcribe the academic year as printed."),
    ),
)
_CATALOG = catalog(_FIELDS, 3)
_CAPABILITIES = Capabilities(frozenset((FieldKind("text"),)))
_PROFILE_LIMITS = ProfileLimits(
    max_documents=2,
    max_fields=3,
    max_checks=2,
    max_identifier_length=64,
)
_INTAKE_LIMITS = IntakeLimits(
    max_documents=2,
    max_fields=3,
    max_claims=2,
    max_claim_values=2,
    max_identifier_length=64,
    max_uri_length=256,
)
_DOCUMENT_TYPE = DocumentTypeId("enrollment_certificate")
_VERIFICATION_TYPE = VerificationTypeId("certificate_verification")
_PROFILE = VerificationProfile(
    ProfileRef(ProfileId("enrollment"), ProfileVersion("v1")),
    _DOCUMENT_TYPE,
    _VERIFICATION_TYPE,
    (),
    (),
)


# The block the refactor extracts -------------------------------------------


@dataclass(frozen=True, slots=True)
class CompileFailure:
    """One compilation failure with the attribution `compose` computes."""

    caller_attributable: bool
    codes: tuple[str, ...]


async def compiled(
    payload: Any,
    resolver: Any,
    /,
) -> Result[Any, CompileFailure]:
    """Run `interpret -> resolve` exactly as `compose` runs it today.

    `compose` refuses an uninterpretable payload before the resolver is
    reached, and attributes a resolve failure to the caller only when
    *every* issue code is caller-owned. Both rules are anef's; this
    function only sequences them so a law can observe the pair.
    """
    interpreted = interpret(payload, _INTAKE_LIMITS)
    if isinstance(interpreted, Err):
        return Err(
            CompileFailure(
                caller_attributable=True,
                codes=tuple(str(issue.code) for issue in interpreted.error.issues),
            )
        )
    resolved = await resolve(
        interpreted.value,
        resolver,
        _CATALOG,
        _CAPABILITIES,
        _PROFILE_LIMITS,
    )
    if isinstance(resolved, Err):
        issues = resolved.error.issues
        return Err(
            CompileFailure(
                caller_attributable=all(
                    issue.code in _CALLER_RESOLVE_CODES for issue in issues
                ),
                codes=tuple(str(issue.code) for issue in issues),
            )
        )
    return Ok(resolved.value)


# Profile adapters the laws inject ------------------------------------------


@dataclass(frozen=True, slots=True)
class FixedResolver:
    """Return one profile for the expected key."""

    async def resolve(
        self,
        key: ProfileKey,
        /,
    ) -> Result[VerificationProfile, ResolveFailure]:
        if key != ProfileKey(_DOCUMENT_TYPE, _VERIFICATION_TYPE):
            raise AssertionError("law constructed an unexpected profile key")
        return Ok(_PROFILE)


class ResolverWasCalled(BaseException):
    """Signal that a broad ``except Exception`` in the target cannot swallow.

    anef's `_lookup` deliberately converts any adapter ``Exception`` into
    ``Err(PROFILE_UNAVAILABLE)`` so storage details never reach a reply.
    That is correct, and it also means an ``AssertionError`` raised by an
    injected double is absorbed by the very code under law: the probe
    would report a wrong-attribution failure and never say that the
    resolver was reached. An effect-absence signal therefore has to live
    outside ``Exception`` — measured, not assumed (issue #1, gap G3).
    """


@dataclass(frozen=True, slots=True)
class NeverCalledResolver:
    """Fail loudly if the compilation reaches the resolver at all.

    Effect-absence is expressed by injecting the collaborator, not by tlaw
    spying on it (gap G3 — a decided non-goal). The double is ordinary
    Python and the law still reads as one claim.
    """

    async def resolve(
        self,
        key: ProfileKey,
        /,
    ) -> Result[VerificationProfile, ResolveFailure]:
        raise ResolverWasCalled(
            "the resolver was called for a request that never interpreted"
        )


@dataclass(frozen=True, slots=True)
class ExplodingResolver:
    """A storage adapter that fails with server internals in its message."""

    detail: str

    async def resolve(
        self,
        key: ProfileKey,
        /,
    ) -> Result[VerificationProfile, ResolveFailure]:
        raise ConnectionError(self.detail)


@dataclass(frozen=True, slots=True)
class CancellingResolver:
    """A storage adapter whose lookup is cancelled mid-flight."""

    async def resolve(
        self,
        key: ProfileKey,
        /,
    ) -> Result[VerificationProfile, ResolveFailure]:
        raise asyncio.CancelledError


# Requests -------------------------------------------------------------------


def request_for(
    field_ids: tuple[str, ...], claimed: tuple[str, ...] = ()
) -> VerificationRequest:
    """Build one valid single-document request selecting the given fields."""
    document = DocumentRequest(
        DocumentId("certificate"),
        _DOCUMENT_TYPE,
        LocalSource(AttachmentId("certificate-upload")),
        DeclaredFields(tuple(FieldId(identifier) for identifier in field_ids)),
    )
    claims = (
        (
            ClaimSource(
                SourceId("applicant"),
                tuple(ClaimValue(FieldId(name), "asserted") for name in claimed),
            ),
        )
        if claimed
        else ()
    )
    return VerificationRequest(
        "verification.request.v2",
        VerificationId("tlaw-anef-probe"),
        _VERIFICATION_TYPE,
        claims,
        (document,),
    )


async def resolved_for(
    field_ids: tuple[str, ...],
    resolver: Any,
    /,
    claimed: tuple[str, ...] = (),
) -> Result[Any, ResolveFailure]:
    """Resolve one already-interpreted request, skipping the JSON grammar."""
    return await resolve(
        request_for(field_ids, claimed),
        resolver,
        _CATALOG,
        _CAPABILITIES,
        _PROFILE_LIMITS,
    )


def profile_checking(field_id: str) -> VerificationProfile:
    """A profile whose one server check is bound to the given field."""
    return VerificationProfile(
        ProfileRef(ProfileId("enrollment"), ProfileVersion("v1")),
        _DOCUMENT_TYPE,
        _VERIFICATION_TYPE,
        (),
        (
            MatchCheck(
                CheckId("matches_claim"),
                FieldRef(DOCUMENT_SOURCE, FieldId(field_id)),
                FieldRef(SourceId("applicant"), FieldId(field_id)),
            ),
        ),
    )


CheckedRequests = tlaw.record(
    fields=tlaw.one_of(
        (("person_name",), ("birth_date",), ("person_name", "birth_date"))
    ),
    checked=tlaw.depends(lambda fields: tlaw.one_of(fields)),
)

Selections = tlaw.one_of(
    (
        ("person_name",),
        ("birth_date",),
        ("person_name", "birth_date"),
        ("person_name", "birth_date", "academic_year"),
    )
)

# Field IDs that no catalog entry defines. The catalog holds exactly the
# three fields above, so each of these must fail as the caller's mistake.
UnknownFieldIds = tlaw.one_of(
    (
        "national_id",
        "salary",
        "person_Name",
        "unknown_field",
    )
)

# Payloads no grammar can interpret: wrong root type, missing required
# members, and unknown members.
Uninterpretable = tlaw.one_of(
    (
        None,
        True,
        "a bare string",
        JsonNumber("42"),
        JsonObject(()),
        JsonObject((JsonMember("schema_version", "verification.request.v2"),)),
        JsonObject((JsonMember("unexpected", None),)),
    )
)

Secrets = tlaw.one_of(
    (
        "db-prod-07.internal:5432",
        "postgresql://anef:hunter2@10.0.4.19/profiles",
        "/var/run/secrets/profile-store.key",
    )
)


# A valid request, generated rather than curated (G1) ------------------------
#
# `interpret` takes anef's JSON value tree, so each record's constructor
# builds the JsonObject its level of the grammar expects. The shape below is
# the whole request: payload -> documents -> document -> source + fields.


def json_members(**members: Any) -> JsonObject:
    """Build one JSON object from the drawn members, in declaration order."""
    return JsonObject(tuple(JsonMember(name, value) for name, value in members.items()))


def json_document(
    *,
    id: str,
    type: str,
    source: JsonObject,
    fields: tuple[str, ...],
) -> JsonObject:
    """Build one document, whose `fields` array is `[{"id": ...}, ...]`."""
    return json_members(
        id=id,
        type=type,
        source=source,
        fields=JsonArray(tuple(json_members(id=identifier) for identifier in fields)),
    )


def json_payload(
    *,
    verification_id: str,
    documents: list[JsonObject],
) -> JsonObject:
    """Build the complete request payload around the drawn documents."""
    return json_members(
        schema_version="verification.request.v2",
        verification_id=verification_id,
        verification_type=str(_VERIFICATION_TYPE),
        claims=JsonArray(()),
        documents=JsonArray(tuple(documents)),
    )


Source = tlaw.record(
    json_members,
    attachment_id=tlaw.one_of(("certificate-upload", "scan-002")),
)
Document = tlaw.record(
    json_document,
    id=tlaw.one_of(("certificate", "diploma")),
    type=tlaw.one_of((str(_DOCUMENT_TYPE),)),
    source=Source,
    fields=Selections,
)
# Exactly one document: two would need their IDs to differ by construction,
# which a record cannot state today (see this probe's README).
ValidPayloads = tlaw.record(
    json_payload,
    verification_id=tlaw.one_of(("tlaw-anef-probe", "request-42")),
    documents=tlaw.list_of(Document, min_size=1, max_size=1),
)


def declared_shape(payload: JsonObject) -> tuple[Any, ...]:
    """Read back what the request itself declared, straight from the JSON."""
    members = {member.name: member.value for member in payload.members}
    documents = members["documents"].items
    return (
        members["verification_id"],
        tuple(
            (
                {m.name: m.value for m in document.members}["id"],
                tuple(
                    {m.name: m.value for m in item.members}["id"]
                    for item in {m.name: m.value for m in document.members}[
                        "fields"
                    ].items
                ),
            )
            for document in documents
        ),
    )


def resolved_shape(spec: Any) -> tuple[Any, ...]:
    """Read the same shape back out of the resolved spec."""
    return (
        str(spec.verification_id),
        tuple(
            (str(document.id), tuple(str(field.id) for field in document.fields))
            for document in spec.documents
        ),
    )


# Law 1 ----------------------------------------------------------------------
# The parity law the refactor exists for: for any valid request, compiling it
# yields a spec that carries exactly the caller's declared identity and field
# selection, in order. This is the claim `compile_verification` must keep.
@tlaw.law
def a_valid_request_compiles_to_exactly_what_it_declared(
    payload: ValidPayloads,
) -> tlaw.Outcome:
    return tlaw.returns(Ok, compiled, payload, FixedResolver()).and_then(
        lambda ok: tlaw.same(resolved_shape(ok.value), declared_shape(payload))
    )


# Law 2 ----------------------------------------------------------------------
# Two contracts in one claim: an uninterpretable payload is the caller's
# mistake, and the resolver is never reached. The second half is carried by
# NeverCalledResolver, which raises if the compilation gets that far.
@tlaw.law
def uninterpretable_payloads_refuse_before_the_resolver(
    payload: Uninterpretable,
) -> tlaw.Outcome:
    return tlaw.returns(Err, compiled, payload, NeverCalledResolver()).and_then(
        lambda failure: tlaw.same(
            (failure.error.caller_attributable, failure.error.codes != ()),
            (True, True),
        )
    )


# Law 3 ----------------------------------------------------------------------
# A field ID absent from the catalog is the caller's mistake, not the
# server's: it must stay on the caller side of the attribution boundary.
@tlaw.law
def an_unknown_field_stays_caller_attributable(
    unknown: UnknownFieldIds,
    declared: Selections,
) -> tlaw.Outcome:
    fields = (*declared, unknown)
    return tlaw.returns(Err, resolved_for, fields, FixedResolver()).and_then(
        lambda failure: tlaw.same(
            all(issue.code in _CALLER_RESOLVE_CODES for issue in failure.error.issues),
            True,
        )
    )


# Law 4 ----------------------------------------------------------------------
# An unexpected adapter exception is the server's fault and must surface
# redacted: `_lookup` converts it to PROFILE_UNAVAILABLE, which is outside
# _CALLER_RESOLVE_CODES, and no hostname, DSN, or key path travels with it.
@tlaw.law
def an_adapter_fault_is_server_attributable_and_redacted(
    secret: Secrets,
    declared: Selections,
) -> tlaw.Outcome:
    resolver = ExplodingResolver(f"could not reach {secret}")
    return tlaw.returns(Err, resolved_for, declared, resolver).and_then(
        lambda failure: tlaw.excludes(failure, secret, "ConnectionError", "Traceback")
    )


# Law 5 ----------------------------------------------------------------------
# Cancellation is control flow, not a technical failure: `_lookup` catches
# Exception, so a cancelled adapter must travel out of resolve unchanged
# rather than becoming Err(PROFILE_UNAVAILABLE).
@tlaw.law
def resolver_cancellation_propagates(declared: Selections) -> tlaw.Refusal:
    return tlaw.raises(
        asyncio.CancelledError,
        resolved_for,
        declared,
        CancellingResolver(),
    )


@dataclass(frozen=True, slots=True)
class CheckingResolver:
    """Return a profile whose one server check names the co-varying field."""

    field_id: str

    async def resolve(
        self,
        key: ProfileKey,
        /,
    ) -> Result[VerificationProfile, ResolveFailure]:
        return Ok(profile_checking(self.field_id))


# Law 6 ----------------------------------------------------------------------
# Not one of the issue's five. It exists because reaching anef's server-check
# path needs the profile's check, the caller's field selection, and the
# claim to name the same field. `record` states the first relation between
# sibling fields; the claim follows from the same drawn value. Nothing is
# filtered, so the law is never vacuous.
@tlaw.law
def a_check_on_a_selected_and_claimed_field_resolves(
    request: CheckedRequests,
) -> tlaw.Outcome:
    return tlaw.returns(
        Ok,
        resolved_for,
        request["fields"],
        CheckingResolver(request["checked"]),
        claimed=(request["checked"],),
    )
