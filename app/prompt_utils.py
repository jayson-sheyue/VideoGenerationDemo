from __future__ import annotations

import json
import mimetypes
import re
from urllib.parse import urlparse

from app.config import settings

IMAGE_REF_PATTERN = re.compile(r"@图片(\d+)|@image\s*(\d+)", re.IGNORECASE)
# After @图片N is rewritten, leftover "Image 3" from mixed prompts.
IMAGE_N_PATTERN = re.compile(r"(?<!IMAGE_REF_)\bImage\s+(\d+)\b", re.IGNORECASE)
HTTP_URL_PATTERN = re.compile(r"https?://[^\s\"'<>\]]+", re.IGNORECASE)
# JSON array of quoted URLs, optionally preceded by 参考图： / references:
URL_ARRAY_PATTERN = re.compile(
    r"(?:参考图|references?|image[_ ]?urls?)\s*[:：]?\s*(\[[\s\S]*?\])"
    r"|(\[\s*\"https?://[\s\S]*?\])",
    re.IGNORECASE,
)


def guess_mime_type(url: str, content_type: str | None = None) -> str:
    if content_type:
        mime = content_type.split(";")[0].strip().lower()
        if mime.startswith("image/"):
            return mime

    path = urlparse(url).path
    guessed, _ = mimetypes.guess_type(path)
    if guessed and guessed.startswith("image/"):
        return guessed

    lower = path.lower()
    if lower.endswith(".jpg") or lower.endswith(".jpeg"):
        return "image/jpeg"
    if lower.endswith(".webp"):
        return "image/webp"
    if lower.endswith(".gif"):
        return "image/gif"
    return "image/png"


def _looks_like_image_url(url: str) -> bool:
    path = urlparse(url).path.lower()
    if any(
        path.endswith(ext)
        for ext in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".heic", ".heif")
    ):
        return True
    return url.startswith("http://") or url.startswith("https://")


def _unique_preserve(urls: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for u in urls:
        u = u.strip().rstrip(".,;")
        if not u or u in seen:
            continue
        if not _looks_like_image_url(u):
            continue
        seen.add(u)
        out.append(u)
    return out


def extract_reference_urls(text: str) -> tuple[str, list[str]]:
    """Pull reference image URLs out of a pasted prompt blob.

    Supports:
    - ``参考图：[ "https://...", ... ]``
    - a bare JSON string array of https URLs
    - loose https URLs in the text

    Returns (cleaned_prompt, urls_in_order).
    """
    if not text or not text.strip():
        return "", []

    urls: list[str] = []
    cleaned = text

    for match in URL_ARRAY_PATTERN.finditer(text):
        raw = match.group(1) or match.group(2)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError:
            try:
                parsed = json.loads(re.sub(r",\s*]", "]", raw))
            except json.JSONDecodeError:
                parsed = None
        if isinstance(parsed, list):
            for item in parsed:
                if isinstance(item, str) and item.startswith("http"):
                    urls.append(item)
            cleaned = cleaned.replace(match.group(0), "\n", 1)

    if not urls:
        stripped = text.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            try:
                parsed = json.loads(stripped)
                if isinstance(parsed, list) and all(
                    isinstance(x, str) and x.startswith("http") for x in parsed
                ):
                    return "", _unique_preserve(parsed)
            except json.JSONDecodeError:
                pass

    if not urls:
        urls = [m.group(0) for m in HTTP_URL_PATTERN.finditer(text)]
        for u in urls:
            cleaned = cleaned.replace(u, "")

    cleaned = re.sub(
        r"(?m)^\s*(参考图|references?|image[_ ]?urls?)\s*[:：]?\s*$",
        "",
        cleaned,
    )
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()

    return cleaned, _unique_preserve(urls)


def _download_via_curl(url: str, *, noproxy: bool) -> bytes:
    """Download with curl. noproxy=True forces direct; False uses system proxy/DNS."""
    import subprocess
    import tempfile
    from pathlib import Path

    tmp = tempfile.NamedTemporaryFile(delete=False)
    tmp_path = Path(tmp.name)
    tmp.close()
    try:
        cmd = [
            "curl",
            "-fsSL",
            "--connect-timeout",
            "30",
            "--max-time",
            str(int(settings.image_download_timeout_sec)),
            "-A",
            "Mozilla/5.0 (compatible; VideoGenerationDemo/1.0)",
            "-o",
            str(tmp_path),
        ]
        if noproxy:
            cmd.extend(["--noproxy", "*"])
        cmd.append(url)
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or f"curl exit {result.returncode}")
        data = tmp_path.read_bytes()
        if not data:
            raise RuntimeError("empty body")
        return data
    finally:
        tmp_path.unlink(missing_ok=True)


def _download_via_urllib(url: str, *, use_proxy: bool) -> tuple[bytes, str]:
    import ssl
    import urllib.request

    import certifi

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (compatible; VideoGenerationDemo/1.0; "
            "+https://localhost)"
        ),
        "Accept": "image/avif,image/webp,image/apng,image/*,*/*;q=0.8",
    }
    request = urllib.request.Request(url, headers=headers)
    context = ssl.create_default_context(cafile=certifi.where())
    handlers: list = [urllib.request.HTTPSHandler(context=context)]
    if use_proxy:
        # Honor HTTP(S)_PROXY so DNS/routing through Clash/VPN works.
        handlers.insert(0, urllib.request.ProxyHandler())
    else:
        handlers.insert(0, urllib.request.ProxyHandler({}))
    opener = urllib.request.build_opener(*handlers)
    with opener.open(request, timeout=settings.image_download_timeout_sec) as resp:
        data = resp.read()
        content_type = resp.headers.get("Content-Type")
    if not data:
        raise ValueError("empty body")
    return data, guess_mime_type(url, content_type)


def _download_image_sync(url: str) -> tuple[bytes, str]:
    """Try proxy then direct — some networks need proxy for DNS to OSS."""
    errors: list[str] = []

    for noproxy, label in ((False, "curl+proxy"), (True, "curl+direct")):
        try:
            data = _download_via_curl(url, noproxy=noproxy)
            return data, guess_mime_type(url)
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    for use_proxy, label in ((True, "urllib+proxy"), (False, "urllib+direct")):
        try:
            return _download_via_urllib(url, use_proxy=use_proxy)
        except Exception as exc:
            errors.append(f"{label}: {exc}")

    raise ValueError(
        f"Failed to download reference image ({url}). " + " | ".join(errors)
    )


async def download_image_bytes(url: str) -> tuple[bytes, str]:
    """Download a third-party image URL → (bytes, mime_type)."""
    import asyncio

    return await asyncio.to_thread(_download_image_sync, url)


async def download_image_as_base64(url: str) -> tuple[str, str]:
    """Download a third-party image URL → (base64_data, mime_type)."""
    import base64

    data, mime = await download_image_bytes(url)
    return base64.b64encode(data).decode("utf-8"), mime


_DIALOGUE_PATTERNS = (
    re.compile(r"[“\"「『]([^”\"」』\n]{2,})[”\"」』]"),
    re.compile(r"(?:说|問|问|道|喊|答)\s*[:：]\s*([^\n]{2,})"),
)


def extract_spoken_lines(prompt: str) -> list[str]:
    """Spoken lines the author wrote. URLs in quotes are ignored."""
    found: list[str] = []
    seen: set[str] = set()
    for pattern in _DIALOGUE_PATTERNS:
        for match in pattern.finditer(prompt):
            line = match.group(1).strip().rstrip("。.!?！？")
            if len(line) < 2 or "://" in line:
                continue
            if line not in seen:
                seen.add(line)
                found.append(line)
    return found


def _image_ref_tag(one_based: int) -> str:
    return f"<IMAGE_REF_{one_based - 1}>"


def rewrite_image_refs(prompt: str, image_count: int) -> str:
    """Bind 1-based @图片N / Image N to Omni's 0-based <IMAGE_REF_N> tags."""

    def from_at(match: re.Match[str]) -> str:
        num = int(match.group(1) or match.group(2))
        return _image_ref_tag(num) if num >= 1 else match.group(0)

    rewritten = IMAGE_REF_PATTERN.sub(from_at, prompt)

    def from_image_n(match: re.Match[str]) -> str:
        num = int(match.group(1))
        return _image_ref_tag(num) if num >= 1 else match.group(0)

    rewritten = IMAGE_N_PATTERN.sub(from_image_n, rewritten)
    return rewritten


def frame_lock_prefix(image_count: int) -> str:
    """image_to_video: the attached stills are literal first / last frames."""
    if image_count <= 0:
        return ""
    if image_count == 1:
        return (
            "Use the given image as the literal first frame of the video. "
            "Animate from that exact frame. Do not replace the subject, "
            "restyle the image, or treat it as a loose character reference."
        )
    return (
        "Use the first image as the literal first frame and the second image "
        "as the literal last frame. Interpolate a continuous camera move "
        "between them. Do not replace subjects or restyle either frame."
    )


def reference_lock_prefix(image_count: int) -> str:
    """Official Omni reference tags + hard lock on look, plot, and speech."""
    if image_count <= 0:
        return ""
    refs = " ".join(
        f"<IMAGE_REF_{i}>@Image{i + 1}" for i in range(image_count)
    )
    return (
        f"[# References {refs}]\n"
        "Use the given image(s) as references for video generation. "
        "The images should not be used as literal initial frames. "
        "Character design, face, costume, scene, key props, and art style "
        "must match the tagged images exactly. Do not photorealize, restyle, "
        "or replace them.\n"
        "Follow the shot list exactly. Do not add shots, actions, characters, "
        "props, locations, or plot that is not written there."
    )


def dialogue_lock(prompt: str) -> str:
    lines = extract_spoken_lines(prompt)
    if not lines:
        return (
            "No dialogue. No speech, narration, or extra spoken lines. "
            "Diegetic sound only."
        )
    listed = " / ".join(f"「{line}」" for line in lines)
    return (
        f"The only spoken dialogue allowed is: {listed}. "
        "Do not add, translate, improvise, or extend any other lines."
    )


def build_control_prefix(
    *,
    duration: str,
    resolution: str,
    aspect_ratio: str,
    generate_audio: bool,
    no_subtitles: bool,
    no_bgm: bool,
    spoken_lines: list[str] | None = None,
) -> str:
    parts = [
        f"{duration} video",
        f"{resolution} resolution",
        f"{aspect_ratio} aspect ratio",
    ]
    if not generate_audio:
        audio = "Mute / no audio track if possible; silence preferred."
    elif spoken_lines:
        audio = "Diegetic audio. Speak only the dialogue locked below."
    else:
        audio = "Diegetic audio only. No speech."
    extras = [audio]
    if no_bgm:
        extras.append("No background music.")
    if no_subtitles:
        extras.append("No burned-in subtitles or on-screen text captions.")
    body = ", ".join(p for p in parts if p)
    return f"{body}. {' '.join(extras)}"


EXAMPLE_IMAGES = [
    "https://www.gstatic.com/webp/gallery/1.jpg",
    "https://www.gstatic.com/webp/gallery/2.jpg",
    "https://www.gstatic.com/webp/gallery/4.jpg",
]

EXAMPLE_PROMPT = (
    """16:9, illustrated look
Character: traveler @图片1
Companion: small fox @图片2
Scene: @图片3
Shot 1, 0-4s, wide, 24mm, f/4, @图片1 and @图片2 stand on a hillside. A lantern lifts off the ground and slowly spins as it rises.
Shot 2, 4-8s, medium, 50mm, f/1.8, high angle. @图片2 and @图片1 look up. The traveler says: What is it looking for?
No burned-in subtitles
No background music

参考图：
"""
    + json.dumps(EXAMPLE_IMAGES, indent=2, ensure_ascii=False)
)

# Clickable walkthrough prompts. Image URLs are public gstatic stills so
# "Load example" works without a local file. Users can replace them with uploads.
GENERATE_EXAMPLES: dict[str, list[dict]] = {
    "text_to_video": [
        {
            "label": "庭院灯笼",
            "prompt": (
                "16:9 cinematic, dusk, illustrated look\n"
                "A red paper lantern lifts off a wooden table in a quiet courtyard. "
                "Wide shot, 24mm, slow tilt up as it rises and spins. "
                "Warm lantern light, cool blue sky. No people.\n"
                "No dialogue. No burned-in subtitles. No background music."
            ),
        },
        {
            "label": "竖屏夜市",
            "prompt": (
                "9:16 handheld documentary\n"
                "A street-food stall at night. Steam rises from a wok. "
                "The cook flips noodles in one continuous motion. Neon signs bokeh in the background.\n"
                "No spoken lines. Diegetic sizzle only. No burned-in subtitles."
            ),
            "aspect_ratio": "9:16",
        },
        {
            "label": "微距水滴",
            "prompt": (
                "16:9 macro, 8 seconds\n"
                "A single water droplet hangs from a leaf tip, then falls in slow motion "
                "and ripples a dark pond. Shallow depth of field, f/1.8, 100mm.\n"
                "Silence except a soft splash. No text on screen."
            ),
        },
    ],
    "image_to_video": [
        {
            "label": "从静帧推进",
            "prompt": (
                "Use this image as the first frame.\n"
                "The camera slowly pushes in. A light breeze moves foliage. "
                "Keep the original lighting and composition; only add natural motion.\n"
                "No dialogue. No burned-in subtitles. No background music."
            ),
            "image_urls": [EXAMPLE_IMAGES[0]],
        },
        {
            "label": "天气变化",
            "prompt": (
                "Start from this exact frame.\n"
                "Over 8 seconds the light cools and a light rain begins. "
                "Gentle handheld drift. Do not change the subject or crop.\n"
                "Diegetic rain only. No speech."
            ),
            "image_urls": [EXAMPLE_IMAGES[1]],
        },
    ],
    "frames_to_video": [
        {
            "label": "两帧运镜",
            "prompt": (
                "Start on the first image and end on the second image.\n"
                "A smooth 8-second camera move interpolates between them: "
                "slow dolly plus a slight pan. Keep lighting consistent.\n"
                "No dialogue. No burned-in subtitles."
            ),
            "image_urls": [EXAMPLE_IMAGES[0], EXAMPLE_IMAGES[2]],
        },
        {
            "label": "首尾循环",
            "prompt": (
                "First image is frame 0, second image is the last frame.\n"
                "Orbit slowly around the subject so the end pose matches the last still. "
                "Motion should feel like one shot, not a cut.\n"
                "No speech. No on-screen text."
            ),
            "image_urls": [EXAMPLE_IMAGES[1], EXAMPLE_IMAGES[0]],
        },
    ],
    "reference_to_video": [
        {
            "label": "角色锁定分镜",
            "prompt": EXAMPLE_PROMPT,
            "image_urls": EXAMPLE_IMAGES,
        },
        {
            "label": "双人仰望",
            "prompt": (
                "16:9, illustrated look\n"
                "Character: traveler @图片1\n"
                "Companion: small fox @图片2\n"
                "Shot 1, 0-8s, medium, 50mm. @图片1 kneels and @图片2 steps closer. "
                "They both look up as a lantern rises out of frame.\n"
                "The traveler says: What is it looking for?\n"
                "No burned-in subtitles. No background music.\n\n"
                "参考图：\n"
                + json.dumps(EXAMPLE_IMAGES[:2], indent=2, ensure_ascii=False)
            ),
            "image_urls": EXAMPLE_IMAGES[:2],
        },
    ],
}

EDIT_EXAMPLES = [
    "Make the lighting warmer, like golden hour.",
    "Remove the coffee cup from the table.",
    "Change her jacket to bright red.",
    "Make it rain lightly outside the window.",
]

EXTEND_EXAMPLES = [
    "Continue as the camera slowly pulls back to reveal the whole space.",
    "Continue as she turns toward the window and smiles.",
    "Continue as the lantern drifts higher and the light fades.",
]


def examples_payload() -> dict:
    return {
        "generate": GENERATE_EXAMPLES,
        "continue": EDIT_EXAMPLES,
        "edit": EDIT_EXAMPLES,
        "extend": EXTEND_EXAMPLES,
    }
