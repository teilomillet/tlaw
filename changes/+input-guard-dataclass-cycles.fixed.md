Traverse `@dataclass` fields and cyclic container pairs in `@tlaw.law`'s input-mutation guard so unchanged NaN fields and self-referential structures are not misreported as mutated inputs.
