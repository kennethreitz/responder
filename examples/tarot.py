"""A tarot deck API with card browsing and seeded deals.

Run it:

    responder run examples/tarot.py

Try it with:

    curl http://127.0.0.1:5042/cards
    curl http://127.0.0.1:5042/cards/the-fool
    curl -H "Content-Type: application/json" \
         -d '{"spread": "past-present-future", "seed": "responder"}' \
         http://127.0.0.1:5042/deal
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, Field

import responder

Arcana = Literal["major", "minor"]
Orientation = Literal["upright", "reversed"]
Suit = Literal["wands", "cups", "swords", "pentacles"]


class TarotCard(BaseModel):
    slug: str = Field(examples=["the-fool"])
    name: str = Field(examples=["The Fool"])
    arcana: Arcana = Field(examples=["major"])
    suit: Suit | None = Field(default=None, examples=["cups"])
    rank: str | None = Field(default=None, examples=["Ace"])
    number: int | None = Field(default=None, examples=[0])
    keywords: list[str] = Field(examples=[["beginnings", "trust", "leap"]])
    upright: str = Field(examples=["Begin before the whole map is visible."])
    reversed: str = Field(examples=["Pause before mistaking impulse for freedom."])


class DrawnCard(BaseModel):
    position: str = Field(examples=["Past"])
    orientation: Orientation = Field(examples=["upright"])
    meaning: str
    card: TarotCard


class DealRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=10, examples=[3])
    spread: str = Field(default="past-present-future", examples=["past-present-future"])
    seed: str | None = Field(
        default=None,
        description="Optional seed for a repeatable example deal.",
        examples=["responder"],
    )
    allow_reversed: bool = Field(default=True)


class DealOut(BaseModel):
    count: int
    spread: str
    seed: str | None
    cards: list[DrawnCard]


class SpreadOut(BaseModel):
    slug: str
    positions: list[str]


MAJOR_ARCANA: tuple[tuple[str, int, list[str], str, str], ...] = (
    (
        "The Fool",
        0,
        ["beginnings", "trust", "leap"],
        "Begin before the whole map is visible.",
        "Pause before mistaking impulse for freedom.",
    ),
    (
        "The Magician",
        1,
        ["focus", "skill", "manifestation"],
        "Use the tools already on the table.",
        "Scattered attention is diluting real power.",
    ),
    (
        "The High Priestess",
        2,
        ["intuition", "mystery", "listening"],
        "Let the quiet signal outrank the obvious noise.",
        "Do not outsource what your instincts already know.",
    ),
    (
        "The Empress",
        3,
        ["growth", "care", "abundance"],
        "Nurture the thing until it becomes generous.",
        "Care has become control; loosen your grip.",
    ),
    (
        "The Emperor",
        4,
        ["structure", "authority", "stability"],
        "Give the work a container strong enough to hold it.",
        "Rules are helping less than they are hardening.",
    ),
    (
        "The Hierophant",
        5,
        ["tradition", "teaching", "belonging"],
        "Learn the lineage before rewriting the ritual.",
        "Inherited rules may be asking for fresh consent.",
    ),
    (
        "The Lovers",
        6,
        ["choice", "union", "values"],
        "Choose from alignment, not appetite alone.",
        "A divided yes is quietly becoming a no.",
    ),
    (
        "The Chariot",
        7,
        ["drive", "discipline", "direction"],
        "Hold the reins and keep moving with intention.",
        "Force without direction is burning the wheels.",
    ),
    (
        "Strength",
        8,
        ["courage", "patience", "heart"],
        "Gentleness is the durable form of power here.",
        "Bravery is being confused with pressure.",
    ),
    (
        "The Hermit",
        9,
        ["solitude", "search", "wisdom"],
        "Step back far enough to hear your own lantern.",
        "Isolation has stopped being restorative.",
    ),
    (
        "Wheel of Fortune",
        10,
        ["cycles", "change", "timing"],
        "The wheel is turning; move with the pattern.",
        "Trying to freeze the cycle is creating friction.",
    ),
    (
        "Justice",
        11,
        ["truth", "balance", "accountability"],
        "Name the facts cleanly and let them rebalance the room.",
        "Something is being weighed with a hidden thumb on the scale.",
    ),
    (
        "The Hanged Man",
        12,
        ["pause", "surrender", "perspective"],
        "A useful answer appears when you stop forcing motion.",
        "Waiting has become avoidance in a softer coat.",
    ),
    (
        "Death",
        13,
        ["ending", "transition", "release"],
        "Let the finished thing be finished.",
        "Refusing the ending is stretching the pain.",
    ),
    (
        "Temperance",
        14,
        ["blend", "patience", "integration"],
        "Mix carefully; the medicine is in the proportion.",
        "Balance is being used as an excuse not to choose.",
    ),
    (
        "The Devil",
        15,
        ["attachment", "shadow", "temptation"],
        "Look directly at the bargain you keep renewing.",
        "A chain is loosening, but habit still calls it necessary.",
    ),
    (
        "The Tower",
        16,
        ["rupture", "truth", "liberation"],
        "The unstable structure is making its condition known.",
        "The warning signs are smaller now; listen before they grow.",
    ),
    (
        "The Star",
        17,
        ["hope", "renewal", "guidance"],
        "Recover under a sky that remembers the way forward.",
        "Hope needs one practical gesture to become real again.",
    ),
    (
        "The Moon",
        18,
        ["dreams", "uncertainty", "subconscious"],
        "Move slowly while the path is silver and strange.",
        "Fear is editing the story before the facts arrive.",
    ),
    (
        "The Sun",
        19,
        ["joy", "clarity", "success"],
        "Let the obvious good news be as simple as it is.",
        "A bright outcome still needs honest stewardship.",
    ),
    (
        "Judgement",
        20,
        ["calling", "reckoning", "awakening"],
        "Answer the call from the self you are becoming.",
        "Old guilt is trying to impersonate accountability.",
    ),
    (
        "The World",
        21,
        ["completion", "wholeness", "arrival"],
        "Close the circle and honor the distance traveled.",
        "Completion is near, but one loose thread wants attention.",
    ),
)

SUIT_THEMES: dict[Suit, tuple[str, str]] = {
    "wands": ("spark", "creative fire, will, and momentum"),
    "cups": ("tide", "feeling, memory, and relationship"),
    "swords": ("edge", "thought, truth, and conflict"),
    "pentacles": ("root", "body, resources, and practical care"),
}

RANK_THEMES: tuple[tuple[str, list[str], str], ...] = (
    ("Ace", ["seed", "gift", "opening"], "a new current entering the story"),
    ("Two", ["choice", "meeting", "balance"], "two forces learning each other"),
    ("Three", ["growth", "collaboration", "signal"], "the first visible result"),
    ("Four", ["foundation", "pause", "shape"], "a stable pattern forming"),
    ("Five", ["friction", "loss", "lesson"], "pressure that reveals priorities"),
    ("Six", ["repair", "memory", "movement"], "a turn toward restoration"),
    ("Seven", ["test", "strategy", "threshold"], "a challenge asking for nerve"),
    ("Eight", ["motion", "practice", "change"], "momentum that rewards attention"),
    ("Nine", ["ripening", "resilience", "solitude"], "the late-stage lesson"),
    ("Ten", ["completion", "weight", "harvest"], "the full consequence arriving"),
    ("Page", ["message", "curiosity", "study"], "the beginner's signal"),
    ("Knight", ["pursuit", "force", "quest"], "motion with a pronounced agenda"),
    ("Queen", ["care", "mastery", "reception"], "mature inner authority"),
    ("King", ["leadership", "stewardship", "command"], "outer authority in action"),
)

SPREADS: dict[str, tuple[str, ...]] = {
    "single": ("Card",),
    "past-present-future": ("Past", "Present", "Future"),
    "mind-body-spirit": ("Mind", "Body", "Spirit"),
    "situation-action-outcome": ("Situation", "Action", "Outcome"),
    "celtic-cross": (
        "Present",
        "Challenge",
        "Foundation",
        "Recent Past",
        "Possibility",
        "Near Future",
        "Self",
        "Environment",
        "Hopes and Fears",
        "Outcome",
    ),
}


def _slugify(name: str) -> str:
    return name.lower().replace(" ", "-").replace("'", "")


def _minor_card_name(rank: str, suit: Suit) -> str:
    return f"{rank} of {suit.title()}"


def _build_deck() -> tuple[TarotCard, ...]:
    cards: list[TarotCard] = []
    for name, number, keywords, upright, reversed_text in MAJOR_ARCANA:
        cards.append(
            TarotCard(
                slug=_slugify(name),
                name=name,
                arcana="major",
                number=number,
                keywords=keywords,
                upright=upright,
                reversed=reversed_text,
            )
        )

    for suit, (symbol, theme) in SUIT_THEMES.items():
        for rank, keywords, rank_meaning in RANK_THEMES:
            name = _minor_card_name(rank, suit)
            cards.append(
                TarotCard(
                    slug=_slugify(name),
                    name=name,
                    arcana="minor",
                    suit=suit,
                    rank=rank,
                    keywords=[symbol, *keywords],
                    upright=f"{name} brings {rank_meaning} through {theme}.",
                    reversed=f"{name} asks where {theme} has become blocked.",
                )
            )
    return tuple(cards)


def _positions_for(spread: str, count: int) -> list[str]:
    positions = list(SPREADS.get(spread, ()))
    if not positions:
        return [f"Card {number}" for number in range(1, count + 1)]
    if len(positions) >= count:
        return positions[:count]
    return positions + [
        f"Card {number}" for number in range(len(positions) + 1, count + 1)
    ]


@dataclass
class TarotDeck:
    cards: tuple[TarotCard, ...] = field(default_factory=_build_deck)
    _by_slug: dict[str, TarotCard] = field(init=False, repr=False)

    def __post_init__(self) -> None:
        self._by_slug = {card.slug: card for card in self.cards}

    def list(
        self,
        *,
        arcana: Arcana | None = None,
        suit: Suit | None = None,
    ) -> list[TarotCard]:
        rows = list(self.cards)
        if arcana is not None:
            rows = [card for card in rows if card.arcana == arcana]
        if suit is not None:
            rows = [card for card in rows if card.suit == suit]
        return [card.model_copy(deep=True) for card in rows]

    def get(self, slug: str) -> TarotCard | None:
        card = self._by_slug.get(slug)
        return None if card is None else card.model_copy(deep=True)

    def deal(self, request: DealRequest) -> DealOut:
        rng = random.Random(request.seed)  # noqa: S311 - tarot shuffles are not security.
        spread = request.spread.strip().lower() or "custom"
        positions = _positions_for(spread, request.count)
        sampled_cards = rng.sample(list(self.cards), k=request.count)

        draws: list[DrawnCard] = []
        for position, card in zip(positions, sampled_cards, strict=True):
            orientation: Orientation = "upright"
            if request.allow_reversed and rng.choice([False, True]):  # noqa: S311
                orientation = "reversed"
            draws.append(
                DrawnCard(
                    position=position,
                    orientation=orientation,
                    meaning=card.reversed if orientation == "reversed" else card.upright,
                    card=card,
                )
            )

        return DealOut(
            count=len(draws),
            spread=spread,
            seed=request.seed,
            cards=draws,
        )


def create_api(*, deck: TarotDeck | None = None) -> responder.API:
    deck = deck or TarotDeck()
    api = responder.API(
        title="Tarot Deck API",
        version="1.0",
        openapi="3.1.0",
        docs_route="/docs",
        sessions=False,
    )

    @api.get("/", include_in_schema=False)
    def index(req, resp):
        resp.media = {
            "name": "Tarot Deck API",
            "cards": "/cards",
            "deal": "/deal",
            "spreads": "/spreads",
            "docs": "/docs",
        }

    @api.get(
        "/cards",
        operation_id="list_tarot_cards",
        tags=["tarot"],
        summary="List tarot cards",
        response_model=list[TarotCard],
    )
    def list_cards(
        req,
        resp,
        *,
        arcana: Arcana | None = responder.Query(
            None,
            description="Filter by major or minor arcana.",
        ),
        suit: Suit | None = responder.Query(
            None,
            description="Filter minor cards by suit.",
        ),
    ):
        resp.media = deck.list(arcana=arcana, suit=suit)

    @api.get(
        "/cards/{slug}",
        operation_id="get_tarot_card",
        tags=["tarot"],
        summary="Fetch a tarot card",
        response_model=TarotCard,
        responses={404: "Card not found"},
    )
    def get_card(req, resp, *, slug: str):
        card = deck.get(slug)
        if card is None:
            resp.problem(404, f"Tarot card {slug!r} does not exist.", slug=slug)
            return
        resp.media = card

    @api.get(
        "/spreads",
        operation_id="list_tarot_spreads",
        tags=["tarot"],
        summary="List built-in spreads",
        response_model=list[SpreadOut],
    )
    def list_spreads(req, resp):
        resp.media = [
            SpreadOut(slug=slug, positions=list(positions))
            for slug, positions in SPREADS.items()
        ]

    @api.post(
        "/deal",
        operation_id="deal_tarot_cards",
        tags=["tarot"],
        summary="Deal tarot cards",
        response_model=DealOut,
    )
    def deal_cards(req, resp, *, deal: DealRequest):
        resp.media = deck.deal(deal)

    return api


api = create_api()


if __name__ == "__main__":
    api.run()
