from app.mastery import P_INIT, bkt_update, next_mode
from app.models import Mode, Verdict


def test_correct_raises_wrong_lowers():
    assert bkt_update(P_INIT, Verdict.correct) > P_INIT
    assert bkt_update(P_INIT, Verdict.wrong) < P_INIT


def test_partial_is_between():
    c, p, w = (bkt_update(P_INIT, v) for v in (Verdict.correct, Verdict.partial, Verdict.wrong))
    assert w < p < c


def test_two_correct_reaches_challenge():
    p = bkt_update(bkt_update(P_INIT, Verdict.correct), Verdict.correct)
    assert p > 0.8
    assert next_mode(Mode.normal, p, 2) == Mode.challenge


def test_support_needs_two_attempts():
    p1 = bkt_update(P_INIT, Verdict.wrong)
    assert p1 < 0.4
    assert next_mode(Mode.normal, p1, 1) == Mode.normal  # one slip isn't enough
    p2 = bkt_update(p1, Verdict.wrong)
    assert next_mode(Mode.normal, p2, 2) == Mode.support


def test_hysteresis():
    assert next_mode(Mode.support, 0.5, 3) == Mode.support
    assert next_mode(Mode.support, 0.65, 3) == Mode.normal
    assert next_mode(Mode.challenge, 0.7, 3) == Mode.challenge
    assert next_mode(Mode.challenge, 0.55, 3) == Mode.normal
