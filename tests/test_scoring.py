from prospecting.db import Lead
from prospecting.scoring import score


def _lead(**kw) -> Lead:
    base = dict(kind="agency", name="Agence Exemple", emails=[], phones=[], people=[], tags=[],
                sources=[], do_not_contact=False)  # fmt: skip
    return Lead(**{**base, **kw})


def test_leaning_photos_and_contacts_rank_high():
    best = _lead(
        headcount="02",
        emails=[{"value": "contact@exemple-immo.fr", "source": "x"}],
        phones=[{"value": "03 88 00 00 00", "source": "x"}],
        people=[{"name": "Jean Exemple", "role": None, "source": "x"}],
        lean_median=4.2,
        listings_seen=12,
        distance_km=3,
    )
    s, reasons = score(best)
    assert s >= 90
    assert any("lean 4.2" in r for r in reasons)


def test_straight_photos_rank_below_leaning_ones():
    straight, _ = score(_lead(lean_median=0.3))
    leaning, _ = score(_lead(lean_median=2.0))
    assert leaning > straight


def test_syndic_only_and_large_groups_are_penalised():
    plain, _ = score(_lead())
    syndic, _ = score(_lead(tags=["syndic"]))
    group, _ = score(_lead(company_category="GE"))
    assert syndic < plain and group < plain


def test_do_not_contact_is_zero():
    assert score(_lead(do_not_contact=True, lean_median=5)) == (0, ["do not contact"])
