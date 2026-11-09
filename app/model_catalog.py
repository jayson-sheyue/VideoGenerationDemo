"""Capability profiles for Omni Flash models served by this demo.

Numbers follow the Gemini Enterprise Agent Platform model card and video
guides (checked 2026-11-06). A custom model id is sent as typed; the profile
only decides which controls this demo will offer.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.config import settings

LABEL_KEY = re.compile(r"^[a-z][a-z0-9_-]{0,62}$")
LABEL_VALUE = re.compile(r"^[a-z0-9_-]{0,63}$")
RESOLUTIONS = ("360p", "720p", "1080p", "4k")
ASPECT_RATIOS = ("16:9", "9:16")
SEED_MAX = 4_294_967_295


@dataclass(frozen=True)
class ModelProfile:
    id: str
    label: str
    stage: str
    generate_min: int
    generate_max: int
    frames_max: int
    edit_max: float
    extend_min: int
    extend_max: int
    # None: any input length; the model keeps up to the last 30s and extends that.
    extend_input_max: float | None
    # None: this demo will not send task=upscale.
    upscale_max: float | None
    reference_images_max: int
    edit_reference_images_max: int
    reference_videos_max: int
    supports_seed: bool
    supports_labels: bool
    supports_multiturn: bool
    audio_input: str
    resolutions: tuple[str, ...] = RESOLUTIONS

    def to_public(self) -> dict:
        return {
            "id": self.id,
            "label": self.label,
            "stage": self.stage,
            "generate_min": self.generate_min,
            "generate_max": self.generate_max,
            "frames_max": self.frames_max,
            "edit_max": self.edit_max,
            "extend_min": self.extend_min,
            "extend_max": self.extend_max,
            "extend_input_max": self.extend_input_max,
            "upscale_max": self.upscale_max,
            "reference_images_max": self.reference_images_max,
            "edit_reference_images_max": self.edit_reference_images_max,
            "reference_videos_max": self.reference_videos_max,
            "supports_seed": self.supports_seed,
            "supports_labels": self.supports_labels,
            "supports_multiturn": self.supports_multiturn,
            "supports_upscale": self.upscale_max is not None,
            "supports_reference_video": self.reference_videos_max > 0,
            "audio_input": self.audio_input,
            "resolutions": list(self.resolutions),
            "aspect_ratios": list(ASPECT_RATIOS),
        }


OMNI_12 = ModelProfile(
    id="gemini-omni-1.2-flash",
    label="Omni 1.2 Flash",
    stage="GA",
    generate_min=3,
    generate_max=15,
    frames_max=10,
    edit_max=15,
    extend_min=3,
    extend_max=10,
    extend_input_max=None,
    upscale_max=15,
    reference_images_max=10,
    edit_reference_images_max=2,
    reference_videos_max=3,
    supports_seed=True,
    supports_labels=True,
    supports_multiturn=True,
    audio_input="allowlist",
)

OMNI_11 = ModelProfile(
    id="gemini-omni-1.1-flash-preview",
    label="Omni 1.1 Flash",
    stage="Preview",
    generate_min=3,
    generate_max=10,
    frames_max=10,
    edit_max=10,
    extend_min=3,
    extend_max=10,
    extend_input_max=30,
    upscale_max=None,
    reference_images_max=10,
    edit_reference_images_max=0,
    reference_videos_max=3,
    supports_seed=False,
    supports_labels=False,
    supports_multiturn=False,
    audio_input="unsupported",
)

CATALOG: dict[str, ModelProfile] = {
    OMNI_12.id: OMNI_12,
    OMNI_11.id: OMNI_11,
}


def resolve_model(requested: str | None) -> tuple[str, ModelProfile, bool]:
    """Return (model id to send, capability profile, whether the profile was assumed)."""
    raw = (requested or settings.omni_model or OMNI_12.id).strip()
    known = CATALOG.get(raw)
    if known:
        return raw, known, False
    template = OMNI_12 if "1.2" in raw else OMNI_11
    assumed = ModelProfile(
        id=raw,
        label=raw,
        stage=template.stage,
        generate_min=template.generate_min,
        generate_max=template.generate_max,
        frames_max=template.frames_max,
        edit_max=template.edit_max,
        extend_min=template.extend_min,
        extend_max=template.extend_max,
        extend_input_max=template.extend_input_max,
        upscale_max=template.upscale_max,
        reference_images_max=template.reference_images_max,
        edit_reference_images_max=template.edit_reference_images_max,
        reference_videos_max=template.reference_videos_max,
        supports_seed=template.supports_seed,
        supports_labels=template.supports_labels,
        supports_multiturn=template.supports_multiturn,
        audio_input=template.audio_input,
        resolutions=template.resolutions,
    )
    return raw, assumed, True


def profile_for(model_id: str | None) -> ModelProfile:
    _model_id, profile, _assumed = resolve_model(model_id)
    return profile


def duration_bounds(profile: ModelProfile, *, mode: str, generate_task: str | None) -> tuple[int, int]:
    if mode == "extend":
        return profile.extend_min, profile.extend_max
    if mode in {"continue", "edit", "upscale"}:
        return profile.generate_min, profile.generate_max
    high = profile.generate_max
    if (generate_task or "") == "frames_to_video":
        high = min(high, profile.frames_max)
    return profile.generate_min, high


def normalize_labels(raw: dict[str, str] | None) -> dict[str, str]:
    if not raw:
        return {}
    cleaned: dict[str, str] = {}
    for key, value in raw.items():
        k = str(key).strip().lower()
        v = str(value).strip().lower()
        if not k and not v:
            continue
        if not LABEL_KEY.match(k) or not LABEL_VALUE.match(v):
            raise ValueError(
                f"label {key}={value} must match Cloud labels: "
                "key starts with a letter, value optional, both lowercase "
                "[a-z0-9_-], at most 63 characters"
            )
        cleaned[k] = v
    if len(cleaned) > 64:
        raise ValueError("at most 64 labels")
    return cleaned
