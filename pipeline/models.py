"""Pydantic mirrors of contracts/*.schema.json.

The same classes double as Gemini response schemas, so they avoid dict-typed
fields and defaults the API can't express.
"""

from typing import Literal

from pydantic import BaseModel, Field


class Vec3(BaseModel):
    x: float
    y: float
    z: float


class Size(BaseModel):
    w: float
    h: float
    d: float


class Wall(BaseModel):
    from_: list[float] = Field(alias="from", description="[x, z] floor point")
    to: list[float] = Field(description="[x, z] floor point")
    height: float

    model_config = {"populate_by_name": True}


class Opening(BaseModel):
    type: Literal["door", "window"]
    wall: int = Field(description="index into this room's walls")
    offset: float = Field(description="meters from the wall's `from` end to the opening's near edge")
    width: float


class Room(BaseModel):
    id: str
    walls: list[Wall]
    openings: list[Opening] = []


class Keyframe(BaseModel):
    t: float = Field(description="seconds into the video")
    position: Vec3
    look_at: Vec3
    fov: float | None = None


class Price(BaseModel):
    low: float
    high: float
    currency: str
    sources: list[str]
    basis: str


class Item(BaseModel):
    id: str
    label: str
    anchor: Vec3
    size: Size
    seen_at: list[float]
    confidence: float
    price: Price | None = None
    status: Literal["present", "moved", "stolen"] | None = None


class Score(BaseModel):
    recall: float
    precision: float


class Fidelity(BaseModel):
    ground_truth_count: int
    single_pass: Score | None = None
    after_loop: Score | None = None
    iterations: int = 0


class Scene(BaseModel):
    rooms: list[Room]
    keyframes: list[Keyframe]
    items: list[Item]
    fidelity: Fidelity | None = None

    def dump(self) -> dict:
        return self.model_dump(by_alias=True, exclude_none=True)

    def item(self, item_id: str) -> Item | None:
        return next((i for i in self.items if i.id == item_id), None)

    def next_id(self) -> str:
        nums = [int(i.id.split("_")[-1]) for i in self.items if i.id.split("_")[-1].isdigit()]
        return f"item_{max(nums, default=0) + 1:03d}"


class GroundTruthItem(BaseModel):
    id: str
    label: str
    aliases: list[str] = []
    location: str | None = None


class GroundTruth(BaseModel):
    room: str | None = None
    items: list[GroundTruthItem]


# --- Argon response shapes ---------------------------------------------------


class ArgonItem(BaseModel):
    label: str = Field(description="specific, priceable description: kind, color, material, size class")
    anchor: Vec3 = Field(description="center of the item's bounding box, meters")
    size: Size
    seen_at: list[float] = Field(description="1-3 video timestamps (seconds) where the item is clearly visible")
    confidence: float


class ArgonScene(BaseModel):
    rooms: list[Room]
    keyframes: list[Keyframe]
    items: list[ArgonItem]


class Critique(BaseModel):
    keyframe: int = Field(description="index of the keyframe pair this critique comes from")
    target: str = Field(description="item id (e.g. item_004), 'new' for a missing item, or 'camera'")
    problem: str = Field(description="one short sentence a person can read, e.g. 'TV on the wall is missing from the render'")
    fix: Literal["add", "move", "resize", "remove", "relabel", "camera"]
    label: str | None = Field(default=None, description="for add / relabel")
    anchor: Vec3 | None = Field(default=None, description="for add / move")
    size: Size | None = Field(default=None, description="for add / resize")
    position: Vec3 | None = Field(default=None, description="for camera: corrected camera position")
    look_at: Vec3 | None = Field(default=None, description="for camera: corrected look-at point")


class CritiqueList(BaseModel):
    critiques: list[Critique]


class MissingItems(BaseModel):
    items: list[ArgonItem]


class Verdict(BaseModel):
    id: str
    visible: bool
    reason: str


class VerdictList(BaseModel):
    verdicts: list[Verdict]
