"""kvfit probe laws, second run: bare form with tlaw.integer domains."""

import math

import tlaw

from kvfit.hardware import Hardware
from kvfit.models import GIB, CacheComponent, CacheEstimate
from kvfit.planner import plan_topologies, summarize_concurrency


Gpus = tlaw.integer(1, 8)
Heads = tlaw.integer(1, 64)
KvGib = tlaw.number(0.001, 64)
WeightBytes = tlaw.integer(0, 512 * GIB)
MemoryGib = tlaw.number(1, 256)
Utilization = tlaw.number(0.05, 1)
Boost = tlaw.number(0, 0.95)


def estimate(kv_gib, query_heads):
    return CacheEstimate(
        architecture="probe",
        context_tokens=1,
        components=(CacheComponent(name="kv", bytes=kv_gib * GIB, detail="probe"),),
        kv_parallel_heads=query_heads,
        query_heads=query_heads,
        confidence="probe",
        reference="probe",
    )


def plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization):
    return plan_topologies(
        estimate(kv_gib, heads),
        weight_bytes=weight_bytes,
        hardware=Hardware(id="probe", label="probe", memory_gib=memory_gib),
        gpus=gpus,
        utilization=utilization,
    )


@tlaw.law
def every_layout_partitions_the_gpus_and_conserves_sequences(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    layouts = plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization)
    return all(
        layout.tensor_parallel * layout.data_parallel == gpus
        and layout.total_sequences
        == layout.sequences_per_replica * layout.data_parallel
        for layout in layouts
    )


@tlaw.law
def every_verdict_is_justified_by_its_own_reported_numbers(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    for layout in plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization):
        budget = layout.budget_per_rank_bytes
        weights = layout.weights_per_rank_bytes
        kv = layout.kv_per_sequence_per_rank_bytes
        held = {
            "unsupported": heads % layout.tensor_parallel != 0,
            "weights-oom": weights > budget and layout.sequences_per_replica == 0,
            "context-oom": weights <= budget
            and weights + kv > budget
            and layout.sequences_per_replica == 0,
            "fits": layout.sequences_per_replica >= 1
            and weights + layout.sequences_per_replica * kv <= budget,
        }.get(layout.verdict, False)
        if not held:
            return False
    return True


@tlaw.law
def raising_utilization_never_lowers_any_layouts_capacity(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
    boost: Boost,
):
    higher = min(1.0, utilization + boost)
    base = plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization)
    more = plan(gpus, heads, kv_gib, weight_bytes, memory_gib, higher)
    return all(
        before.sequences_per_replica <= after.sequences_per_replica
        for before, after in zip(base, more)
    )


@tlaw.law
def heavier_weights_never_raise_capacity(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
    extra: WeightBytes,
):
    base = plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization)
    heavier = plan(gpus, heads, kv_gib, weight_bytes + extra, memory_gib, utilization)
    return all(
        after.sequences_per_replica <= before.sequences_per_replica
        for before, after in zip(base, heavier)
    )


@tlaw.law
def the_budget_is_anchored_to_the_hardware_and_utilization(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    # Anchored to an external fact rather than to the planner's own
    # output: a law that only compares outputs to outputs cannot catch
    # an error in the shared input to both sides.
    expected = memory_gib * GIB * utilization
    return all(
        tlaw.same(layout.budget_per_rank_bytes, expected, within=1e-6)
        for layout in plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization)
    )


# The law that went unwritten in the first kvfit probe: a valid explicit
# tensor-parallel size must divide the GPU count. depends() supplies the
# wiring; one_of() states the computed set of valid choices.
TensorParallel = tlaw.depends(
    lambda gpus: tlaw.one_of([size for size in range(1, gpus + 1) if gpus % size == 0])
)


@tlaw.law
def an_explicit_tensor_parallel_size_is_honoured(
    gpus: Gpus,
    tensor_parallel: TensorParallel,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    layouts = plan_topologies(
        estimate(kv_gib, heads),
        weight_bytes=weight_bytes,
        hardware=Hardware(id="probe", label="probe", memory_gib=memory_gib),
        gpus=gpus,
        utilization=utilization,
        tensor_parallel=tensor_parallel,
    )
    return (
        len(layouts) == 1
        and layouts[0].tensor_parallel == tensor_parallel
        and layouts[0].data_parallel == gpus // tensor_parallel
    )


# Previously unwritable: scale_up_domain_accelerators must divide gpus.
# depends + one_of now expresses that computed-divisor domain, letting the
# law supply the optional argument and exercise its validation + the
# domain-count arithmetic.
ScaleUp = tlaw.depends(
    lambda gpus: tlaw.one_of([d for d in range(1, gpus + 1) if gpus % d == 0])
)


@tlaw.law
def scale_up_domains_are_reported_and_counted(
    gpus: Gpus,
    scale_up: ScaleUp,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    layouts = plan_topologies(
        estimate(kv_gib, heads),
        weight_bytes=weight_bytes,
        hardware=Hardware(id="probe", label="probe", memory_gib=memory_gib),
        gpus=gpus,
        utilization=utilization,
        scale_up_domain_accelerators=scale_up,
    )
    return all(
        layout.scale_up_domain_accelerators == scale_up
        and layout.tensor_parallel_domains
        == (layout.tensor_parallel + scale_up - 1) // scale_up
        for layout in layouts
    )


PerUser = tlaw.integer(1, 8)


@tlaw.law
def as_dict_converts_every_byte_field_to_gib(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    for layout in plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization):
        reported = layout.as_dict()
        if not (
            tlaw.same(
                reported["budget_per_rank_gib"], layout.budget_per_rank_bytes / GIB
            )
            and tlaw.same(
                reported["weights_per_rank_gib"], layout.weights_per_rank_bytes / GIB
            )
            and tlaw.same(
                reported["kv_per_sequence_per_rank_gib"],
                layout.kv_per_sequence_per_rank_bytes / GIB,
            )
        ):
            return False
    return True


@tlaw.law
def weights_and_kv_shard_by_tensor_parallel(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    for layout in plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization):
        tp = layout.tensor_parallel
        if not tlaw.same(
            layout.weights_per_rank_bytes, weight_bytes / tp, relative=1e-9
        ):
            return False
        if layout.kv_per_sequence_per_rank_bytes != math.inf:
            expected = kv_gib * GIB / min(tp, heads)
            if not tlaw.same(
                layout.kv_per_sequence_per_rank_bytes, expected, relative=1e-9
            ):
                return False
    return True


@tlaw.law
def every_gpu_divisor_is_a_tensor_parallel_candidate(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    layouts = plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization)
    present = {layout.tensor_parallel for layout in layouts}
    expected = {size for size in range(1, gpus + 1) if gpus % size == 0}
    return present == expected


@tlaw.law
def concurrency_divides_active_sequences_by_user(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
    per_user: PerUser,
):
    layouts = plan(gpus, heads, kv_gib, weight_bytes, memory_gib, utilization)
    summary = summarize_concurrency(layouts, active_sequences_per_user=per_user)
    if summary["active_sequences_per_user"] != per_user:
        return False
    for key in ("recommended", "best_scale_up_local", "best_any_fabric"):
        capacity = summary[key]
        if capacity is not None:
            if capacity["concurrent_users"] != capacity["active_sequences"] // per_user:
                return False
    return True


BudgetK = tlaw.integer(1, 100)
OneSeqKv = tlaw.number(0.1, 10)
# depends builds the second input from the first: a memory that leaves room
# for exactly one sequence (budget in [kv, ~2*kv)), so the fits boundary at
# sequences == 1 is reached by construction rather than by luck.
OneSeqMem = tlaw.depends(lambda one_kv: tlaw.number(one_kv, 1.99 * one_kv))


@tlaw.law
def zero_utilization_is_refused(
    gpus: Gpus,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
):
    return tlaw.raises(
        ValueError,
        plan_topologies,
        estimate(kv_gib, heads),
        weight_bytes=weight_bytes,
        hardware=Hardware(id="probe", label="probe", memory_gib=memory_gib),
        gpus=gpus,
        utilization=0.0,
    )


@tlaw.law
def the_cross_domain_flag_matches_its_definition(
    gpus: Gpus,
    scale_up: ScaleUp,
    heads: Heads,
    kv_gib: KvGib,
    weight_bytes: WeightBytes,
    memory_gib: MemoryGib,
    utilization: Utilization,
):
    layouts = plan_topologies(
        estimate(kv_gib, heads),
        weight_bytes=weight_bytes,
        hardware=Hardware(id="probe", label="probe", memory_gib=memory_gib),
        gpus=gpus,
        utilization=utilization,
        scale_up_domain_accelerators=scale_up,
    )
    return all(
        layout.cross_domain_tensor_parallel
        == bool(
            scale_up
            and (
                layout.tensor_parallel > scale_up
                or scale_up % layout.tensor_parallel != 0
            )
        )
        for layout in layouts
    )


@tlaw.law
def a_layout_with_room_for_one_sequence_fits(one_kv: OneSeqKv, mem: OneSeqMem):
    layouts = plan_topologies(
        estimate(one_kv, 1),
        weight_bytes=0,
        hardware=Hardware(id="probe", label="probe", memory_gib=mem),
        gpus=1,
        utilization=1.0,
    )
    layout = layouts[0]
    # Assert the reason too: the planner decides "fits" in both a verdict
    # and a reason conditional, so the law must observe both.
    return (
        layout.sequences_per_replica == 1
        and layout.verdict == "fits"
        and "unmodeled" in layout.reason
    )


@tlaw.law
def weights_exactly_filling_the_budget_leave_no_kv_room(
    k: BudgetK, kv_gib: KvGib, heads: Heads
):
    # weight_bytes == budget exactly (both k*GIB), so available_for_kv == 0:
    # the reference proceeds (context-oom), a <=0 boundary would call it
    # weights-oom.
    layouts = plan_topologies(
        estimate(kv_gib, heads),
        weight_bytes=k * GIB,
        hardware=Hardware(id="probe", label="probe", memory_gib=k),
        gpus=1,
        utilization=1.0,
    )
    return layouts[0].verdict == "context-oom"
