from __future__ import annotations

import json
import mimetypes
import re
from urllib.parse import urlparse

from app.config import settings

IMAGE_REF_PATTERN = re.compile(r"@图片(\d+)|@image\s*(\d+)", re.IGNORECASE)
VIDEO_REF_PATTERN = re.compile(r"@视频(\d+)|@video\s*(\d+)", re.IGNORECASE)
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


def _video_ref_tag(one_based: int) -> str:
    return f"<VIDEO_REF_{one_based - 1}>"


def rewrite_video_refs(prompt: str) -> str:
    """Bind 1-based @视频N / @video N to Omni's 0-based <VIDEO_REF_N> tags."""

    def from_at(match: re.Match[str]) -> str:
        num = int(match.group(1) or match.group(2))
        return _video_ref_tag(num) if num >= 1 else match.group(0)

    return VIDEO_REF_PATTERN.sub(from_at, prompt)


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


def reference_lock_prefix(image_count: int, video_count: int = 0) -> str:
    """Official Omni reference tags + hard lock on look, plot, and speech."""
    if image_count <= 0 and video_count <= 0:
        return ""
    tags = [
        f"<IMAGE_REF_{i}>@Image{i + 1}" for i in range(image_count)
    ] + [f"<VIDEO_REF_{i}>@Video{i + 1}" for i in range(video_count)]
    lines = [f"[# References {' '.join(tags)}]"]
    if image_count:
        lines.append(
            "Use the given image(s) as references for video generation. "
            "The images should not be used as literal initial frames. "
            "Character design, face, costume, scene, key props, and art style "
            "must match the tagged images exactly. Do not photorealize, restyle, "
            "or replace them."
        )
    if video_count:
        lines.append(
            "Use the given video(s) as references for likeness, motion, or style. "
            "Do not use them as a source for video editing. "
            "Audio inside a reference video is ignored."
        )
    lines.append(
        "Follow the shot list exactly. Do not add shots, actions, characters, "
        "props, locations, or plot that is not written there."
    )
    return "\n".join(lines)


# Public stills used by every image example. Prompts below describe these
# exact pictures. gallery/1 is a fjord overlook, /2 a kayaker in whitewater,
# /4 a blossoming tree.
STILL_FJORD = "https://www.gstatic.com/webp/gallery/1.jpg"
STILL_KAYAK = "https://www.gstatic.com/webp/gallery/2.jpg"
STILL_TREE = "https://www.gstatic.com/webp/gallery/4.jpg"

# Clickable walkthrough prompts. Each image_urls entry is one of the stills
# above, and the prompt names what that still actually shows.
GENERATE_EXAMPLES: dict[str, list[dict]] = {
    "text_to_video": [
        {
            "label": "庭院灯笼",
            "duration": 8,
            "aspect_ratio": "16:9",
            "prompt": (
                "16:9 cinematic, dusk\n"
                "A red paper lantern lifts off a wooden table in a quiet courtyard. "
                "Wide shot, 24mm, slow tilt up as it rises and spins. "
                "Warm lantern light, cool blue sky. No people.\n"
                "No dialogue. No burned-in subtitles. No background music."
            ),
        },
        {
            "label": "竖屏夜市",
            "duration": 8,
            "aspect_ratio": "9:16",
            "prompt": (
                "9:16 handheld documentary\n"
                "A street-food stall at night. Steam rises from a wok. "
                "The cook flips noodles in one continuous motion. Neon signs bokeh in the background.\n"
                "No spoken lines. Diegetic sizzle only. No burned-in subtitles."
            ),
        },
        {
            "label": "微距水滴",
            "duration": 8,
            "aspect_ratio": "16:9",
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
            "label": "峡湾推近",
            "duration": 8,
            "aspect_ratio": "16:9",
            "prompt": (
                "Use this image as the first frame.\n"
                "It is a sunny cliff overlook above a long blue fjord and steep green mountains. "
                "The camera slowly pushes in along the fjord. Keep the rocky foreground and the valley. "
                "Do not add people, boats, or buildings.\n"
                "No dialogue. No burned-in subtitles. No background music."
            ),
            "image_urls": [STILL_FJORD],
        },
        {
            "label": "急流划桨",
            "duration": 8,
            "aspect_ratio": "16:9",
            "prompt": (
                "Use this image as the first frame.\n"
                "The kayaker in the white helmet and red-and-black jacket completes one paddle stroke "
                "as whitewater breaks around the boat. Keep that helmet, jacket, paddle, and crop. "
                "Do not add a second person or change the river for rain.\n"
                "Diegetic water only. No speech. No burned-in subtitles."
            ),
            "image_urls": [STILL_KAYAK],
        },
    ],
    "frames_to_video": [
        {
            "label": "峡湾到花树",
            "duration": 8,
            "aspect_ratio": "16:9",
            "prompt": (
                "The first image is frame 0: a rocky cliff above a blue fjord. "
                "The second image is the last frame: one white-blossomed tree on a grassy hill under a clear sky.\n"
                "Travel from the fjord overlook to the blossoming tree in one 8-second move. "
                "Do not add people.\n"
                "No dialogue. No burned-in subtitles."
            ),
            "image_urls": [STILL_FJORD, STILL_TREE],
        },
        {
            "label": "花树到峡湾",
            "duration": 8,
            "aspect_ratio": "16:9",
            "prompt": (
                "The first image is frame 0: a single blossoming tree on a green hillside. "
                "The second image is the last frame: the cliff overlook and the blue fjord.\n"
                "Move from the tree to the fjord in one continuous shot. Do not add people.\n"
                "No speech. No on-screen text."
            ),
            "image_urls": [STILL_TREE, STILL_FJORD],
        },
    ],
    "reference_to_video": [
        {
            "label": "峡湾桨手花树",
            "duration": 8,
            "aspect_ratio": "16:9",
            "prompt": (
                "16:9 documentary\n"
                "Fjord overlook: @图片1\n"
                "Kayaker: @图片2\n"
                "Blossoming tree: @图片3\n"
                "Shot 1, 0-3s, wide, 24mm. Glide along the fjord in @图片1. "
                "Keep the rocky cliff in the foreground. No people in this shot.\n"
                "Shot 2, 3-6s, medium, 50mm. The kayaker in @图片2 takes one stroke through whitewater. "
                "Keep the white helmet, red-and-black jacket, and paddle.\n"
                "Shot 3, 6-8s, medium, 50mm. Hold on the white-blossomed tree in @图片3. "
                "A light breeze moves the blossoms. No people.\n"
                "No dialogue. No burned-in subtitles. No background music."
            ),
            "image_urls": [STILL_FJORD, STILL_KAYAK, STILL_TREE],
        },
        {
            "label": "两处风景",
            "duration": 8,
            "aspect_ratio": "16:9",
            "prompt": (
                "16:9, natural light\n"
                "Fjord: @图片1\n"
                "Blossoming tree: @图片2\n"
                "Shot 1, 0-4s, wide, 24mm. The camera moves along the fjord overlook in @图片1. "
                "Keep the cliff and the blue water. No people.\n"
                "Shot 2, 4-8s, medium, 50mm. Hold on the white-blossomed tree in @图片2. "
                "A light breeze moves the blossoms. The hillside stays green. No people.\n"
                "No dialogue. No burned-in subtitles. No background music."
            ),
            "image_urls": [STILL_FJORD, STILL_TREE],
        },
    ],
}

EXAMPLE_IMAGES = list(GENERATE_EXAMPLES["reference_to_video"][0]["image_urls"])
EXAMPLE_PROMPT = GENERATE_EXAMPLES["reference_to_video"][0]["prompt"]

EDIT_EXAMPLES = [
    "Make the lighting warmer, like golden hour. Keep the same subject and framing.",
    "Add a light breeze to whatever is already in the frame. Do not add people or objects.",
    "Cool the color toward blue hour. Do not add or remove anyone.",
    "A slow push-in. Do not change who or what is already in the shot.",
]

EXTEND_EXAMPLES = [
    "Continue as the camera slowly pulls back, showing more of the same place.",
    "Continue as the motion already in the shot carries on. Do not add a new person.",
    "Continue as the light softens and the camera holds on the same subject.",
]


def examples_payload() -> dict:
    return {
        "generate": GENERATE_EXAMPLES,
        "continue": EDIT_EXAMPLES,
        "edit": EDIT_EXAMPLES,
        "extend": EXTEND_EXAMPLES,
    }
