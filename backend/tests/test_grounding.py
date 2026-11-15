from app.guardrails.grounding import is_grounded

SPAN = ("[seq 6 | 69-81s] So what does fmap do? It takes an ordinary function from a to b and lifts it to "
        "work inside the structure. For lists, fmap is just map.")


def test_exact_and_near_quotes_pass():
    assert is_grounded("It takes an ordinary function from a to b", [SPAN])
    assert is_grounded("it takes an ordinary function from a to b and lifts it", [SPAN])
    assert is_grounded("`fmap` is just map", [SPAN])


def test_invented_quote_fails():
    assert not is_grounded("Monads are burritos wrapped in a monoid", [SPAN])


def test_short_or_empty_quote_fails():
    assert not is_grounded("map", [SPAN])
    assert not is_grounded("", [SPAN])


def test_slides_count_as_sources():
    assert is_grounded("fmap id = id", ["Functor laws. Identity: fmap id = id."])
