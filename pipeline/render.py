"""Headless software render of a primitive scene from a keyframe viewpoint.

Pinhole camera, painter's algorithm, near-plane clipping. Each item box gets a
stable color and an id label so Argon can name the item it's critiquing.
"""

import colorsys
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont

from models import Item, Keyframe, Room, Scene

NEAR = 0.05
DEFAULT_FOV = 60.0


class Camera:
    def __init__(self, kf: Keyframe, width: int, height: int):
        self.pos = np.array([kf.position.x, kf.position.y, kf.position.z])
        target = np.array([kf.look_at.x, kf.look_at.y, kf.look_at.z])
        fwd = target - self.pos
        fwd /= np.linalg.norm(fwd) or 1.0
        right = np.cross(fwd, [0.0, 1.0, 0.0])
        if np.linalg.norm(right) < 1e-6:  # looking straight up or down
            right = np.array([1.0, 0.0, 0.0])
        right /= np.linalg.norm(right)
        up = np.cross(right, fwd)
        self.basis = np.stack([right, up, fwd])
        self.w, self.h = width, height
        self.f = (height / 2) / math.tan(math.radians(kf.fov or DEFAULT_FOV) / 2)

    def to_cam(self, pts: np.ndarray) -> np.ndarray:
        return (pts - self.pos) @ self.basis.T

    def project(self, cam_pts: np.ndarray) -> list[tuple[float, float]]:
        return [(self.w / 2 + self.f * x / z, self.h / 2 - self.f * y / z) for x, y, z in cam_pts]


def _clip_near(poly: np.ndarray) -> np.ndarray:
    """Sutherland-Hodgman against z = NEAR in camera space."""
    out = []
    n = len(poly)
    for i in range(n):
        a, b = poly[i], poly[(i + 1) % n]
        a_in, b_in = a[2] >= NEAR, b[2] >= NEAR
        if a_in:
            out.append(a)
        if a_in != b_in:
            t = (NEAR - a[2]) / (b[2] - a[2])
            out.append(a + t * (b - a))
    return np.array(out)


def item_color(item_id: str) -> tuple[int, int, int]:
    n = sum(ord(c) * 31 ** i for i, c in enumerate(item_id))
    r, g, b = colorsys.hsv_to_rgb((n * 0.618034) % 1.0, 0.65, 0.9)
    return int(r * 255), int(g * 255), int(b * 255)


def _shade(rgb: tuple[int, int, int], k: float) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(c * k))) for c in rgb)


def _box_faces(item: Item) -> list[tuple[np.ndarray, float]]:
    c, s = item.anchor, item.size
    x0, x1 = c.x - s.w / 2, c.x + s.w / 2
    y0, y1 = c.y - s.h / 2, c.y + s.h / 2
    z0, z1 = c.z - s.d / 2, c.z + s.d / 2
    v = lambda x, y, z: [x, y, z]
    return [
        (np.array([v(x0, y1, z0), v(x1, y1, z0), v(x1, y1, z1), v(x0, y1, z1)]), 1.0),   # top
        (np.array([v(x0, y0, z0), v(x1, y0, z0), v(x1, y0, z1), v(x0, y0, z1)]), 0.5),   # bottom
        (np.array([v(x0, y0, z1), v(x1, y0, z1), v(x1, y1, z1), v(x0, y1, z1)]), 0.85),  # +z
        (np.array([v(x0, y0, z0), v(x1, y0, z0), v(x1, y1, z0), v(x0, y1, z0)]), 0.75),  # -z
        (np.array([v(x1, y0, z0), v(x1, y0, z1), v(x1, y1, z1), v(x1, y1, z0)]), 0.7),   # +x
        (np.array([v(x0, y0, z0), v(x0, y0, z1), v(x0, y1, z1), v(x0, y1, z0)]), 0.65),  # -x
    ]


def _wall_quads(room: Room) -> list[tuple[np.ndarray, tuple[int, int, int]]]:
    quads = []
    for i, w in enumerate(room.walls):
        (ax, az), (bx, bz) = w.from_, w.to
        quads.append((np.array([[ax, 0, az], [bx, 0, bz], [bx, w.height, bz], [ax, w.height, az]], float), (205, 200, 190)))
        length = math.hypot(bx - ax, bz - az) or 1.0
        ux, uz = (bx - ax) / length, (bz - az) / length
        for o in room.openings:
            if o.wall != i:
                continue
            y0, y1 = (0.0, 2.05) if o.type == "door" else (0.9, 2.1)
            px0, pz0 = ax + ux * o.offset, az + uz * o.offset
            px1, pz1 = px0 + ux * o.width, pz0 + uz * o.width
            color = (120, 95, 75) if o.type == "door" else (150, 190, 220)
            quads.append((np.array([[px0, y0, pz0], [px1, y0, pz1], [px1, y1, pz1], [px0, y1, pz0]], float), color))
    return quads


def _font(size: int) -> ImageFont.ImageFont:
    for name in ("/System/Library/Fonts/Supplemental/Arial.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render(scene: Scene, kf: Keyframe, width: int = 1280, height: int = 720) -> Image.Image:
    cam = Camera(kf, width, height)
    img = Image.new("RGB", (width, height), (40, 42, 48))
    draw = ImageDraw.Draw(img)

    def fill(poly3d: np.ndarray, color, outline=None) -> list[tuple[float, float]] | None:
        clipped = _clip_near(cam.to_cam(poly3d))
        if len(clipped) < 3:
            return None
        pts = cam.project(clipped)
        draw.polygon(pts, fill=color, outline=outline)
        return pts

    # Floor, then walls back-to-front, then openings, then item faces back-to-front.
    for room in scene.rooms:
        floor = np.array([[w.from_[0], 0.0, w.from_[1]] for w in room.walls], float)
        if len(floor) >= 3:
            fill(floor, (95, 90, 85), outline=(70, 66, 62))
    shell = [q for room in scene.rooms for q in _wall_quads(room)]
    walls = [q for q in shell if q[1] == (205, 200, 190)]
    openings = [q for q in shell if q[1] != (205, 200, 190)]
    walls.sort(key=lambda q: -cam.to_cam(q[0])[:, 2].mean())
    for quad, color in walls + openings:
        fill(quad, color, outline=(150, 145, 138))

    faces = []
    for item in scene.items:
        base = item_color(item.id)
        for quad, k in _box_faces(item):
            faces.append((cam.to_cam(quad)[:, 2].mean(), quad, _shade(base, k)))
    faces.sort(key=lambda f: -f[0])
    for _, quad, color in faces:
        fill(quad, color, outline=_shade(color, 0.6))

    font = _font(max(14, height // 40))
    for item in scene.items:
        c = cam.to_cam(np.array([[item.anchor.x, item.anchor.y, item.anchor.z]]))[0]
        if c[2] < NEAR:
            continue
        (u, v), = cam.project([c])
        if not (0 <= u < width and 0 <= v < height):
            continue
        text = f"{item.id} {item.label}"
        box = draw.textbbox((u, v), text, font=font, anchor="mm")
        draw.rectangle((box[0] - 3, box[1] - 2, box[2] + 3, box[3] + 2), fill=(255, 255, 255))
        draw.text((u, v), text, fill=(0, 0, 0), font=font, anchor="mm")
    return img


def side_by_side(real: str | Path, rendered: Image.Image, caption: str = "") -> Image.Image:
    """REAL | RENDER composite, same height. Also what the viewer shows for a loop catch."""
    real_img = Image.open(real).convert("RGB")
    h = real_img.height
    rendered = rendered.resize((int(rendered.width * h / rendered.height), h))
    bar = 36
    out = Image.new("RGB", (real_img.width + rendered.width, h + bar), (20, 20, 20))
    out.paste(real_img, (0, bar))
    out.paste(rendered, (real_img.width, bar))
    d = ImageDraw.Draw(out)
    font = _font(22)
    d.text((10, 6), f"REAL  {caption}", fill=(255, 255, 255), font=font)
    d.text((real_img.width + 10, 6), "RENDER (current scene)", fill=(255, 255, 255), font=font)
    return out
