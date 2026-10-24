"""Small, local daily-inspiration catalog for the calm Today workspace."""

from datetime import date

INSPIRATIONS: tuple[dict[str, str], ...] = (
    {
        "quote": "Well done is better than well said.",
        "attribution": "Benjamin Franklin",
        "meaning": (
            "A small review completed today strengthens your memory more than "
            "an ambitious plan postponed until tomorrow."
        ),
        "provenance": "public_domain",
    },
    {
        "quote": "No great thing is created suddenly.",
        "attribution": "Epictetus",
        "meaning": (
            "Understanding grows through repeated, unhurried encounters. "
            "Each short study session is part of the result."
        ),
        "provenance": "public_domain",
    },
    {
        "quote": "While we teach, we learn.",
        "attribution": "Seneca",
        "meaning": (
            "Explaining an idea in your own words reveals what you know and "
            "gently shows you what deserves another look."
        ),
        "provenance": "public_domain",
    },
    {
        "quote": "Energy and persistence conquer all things.",
        "attribution": "Benjamin Franklin",
        "meaning": (
            "Progress comes from returning consistently, especially on ordinary "
            "days when the effort feels small."
        ),
        "provenance": "public_domain",
    },
    {
        "quote": "Nothing great was ever achieved without enthusiasm.",
        "attribution": "Ralph Waldo Emerson",
        "meaning": (
            "Curiosity gives practice its energy. Follow the part of today's "
            "material that makes you want to understand more."
        ),
        "provenance": "public_domain",
    },
    {
        "quote": "Great works are performed, not by strength, but by perseverance.",
        "attribution": "Samuel Johnson",
        "meaning": (
            "Learning does not require a perfect session. It asks only that you "
            "return, retrieve, and keep moving forward."
        ),
        "provenance": "public_domain",
    },
    {
        "quote": "I am not afraid of storms, for I am learning how to sail my ship.",
        "attribution": "Louisa May Alcott",
        "meaning": (
            "Difficult questions are not evidence that you are failing; they are "
            "practice in navigating ideas with confidence."
        ),
        "provenance": "public_domain",
    },
)


def get_daily_inspiration(local_day: date) -> dict[str, str]:
    """Return a stable catalog item for ``local_day`` without network I/O."""

    item = INSPIRATIONS[local_day.toordinal() % len(INSPIRATIONS)]
    return dict(item)


__all__ = ["INSPIRATIONS", "get_daily_inspiration"]
