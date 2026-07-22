from hypothesis import settings
from hypothesis import strategies as st

import tlaw


text = tlaw.system(lambda value: value, name="text")


@settings(database=None)
@tlaw.law("UTF-8 encode then decode is identity", value=st.text())
def utf8_round_trip(value: str):
    start = text(value)
    return (
        start.then(str.encode, name="encode")
        .then(bytes.decode, name="decode")
        .same_as(start)
    )
