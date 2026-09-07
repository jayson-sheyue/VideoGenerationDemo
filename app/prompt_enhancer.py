"""Optional cinematography pass over a shot list before it reaches Omni.

Omni follows a prompt closely but does not invent much on its own, so a terse
shot list yields flat lighting and hard cuts. This asks a text model to add
craft vocabulary — lighting, lens behaviour, camera movement, transitions —
while leaving the story exactly as written.

Every rewrite is validated before use, and any failure falls back to the
original prompt: a polished prompt is a nice-to-have, never a hard dependency.
"""

from __future__ import annotations

import asyncio
import json
import re
from typing import Any

from app import omni_client
from app.config import settings

GENERATE_SYSTEM = """You add camera craft to a video shot list. Reference images \
are attached to the generation request; the text-to-video model will copy \
whatever the text describes, so extra visual prose WILL override those images.

ADD only, and only where the original is silent:
- Camera: one clear move per shot (dolly, pan, tilt, push-in, handheld drift).
- How consecutive shots join: cut, match cut, or a motivated continuation.

Do NOT add or invent:
- Time of day, weather, colour temperature, or a new lighting setup.
- Materials, ground texture, dust, haze, metal sheen, or other surface detail.
- New locations, props, wardrobe, or characters.
- A different art style (photoreal, cinematic, anime, etc.). Style is decided \
by the reference images and by words already in the shot list.

NEVER change:
- Subjects, actions, shot numbers, timings, dialogue (copy every spoken line \
exactly, original language), or technical settings already given.
- Image tokens: keep @图片N / Image N verbatim, bound to the same subject.

Constraints:
- Appearance of people, scene, and key objects comes from the reference images. \
In each shot, keep those tokens next to the subject and do not re-describe \
what they look like.
- Write in the same language as the input.
- Stay close to the original length: about 1.1x to 1.4x, never more than 1.6x.
- Output only the rewritten shot list. No preamble, no commentary, no markdown."""

MOTION_ADDENDUM_SYSTEM = """You write a short motion addendum to APPEND after a \
shot list. The shot list and reference images already lock look, characters, \
scene, style, and dialogue. Do not rewrite the shot list.

The video model often freezes into a still if motion is only mentioned once. \
Your job is to insist that actions ALREADY in the shot list actually play, and \
to add camera moves that serve those actions.

Output in the same language as the shot list, starting exactly with:
动作与运镜（必须全部发生）：

Then 4–8 short bullet lines:
- Restate every action already written (takeoff, wing unfold, hull rotating, \
scan beam, looking up, …) as "must happen on screen, not a freeze-frame".
- One camera move per shot (tilt up with the ship, slow push-in on faces, …).
- No new plot, characters, props, weather, time of day, or spoken lines.
- Do not describe faces, costumes, art style, or scenery.

Output only the addendum."""

DEFAULT_MOTION_NOTE = (
    "动作与运镜（必须全部发生）：分镜里写到的每个动作都要完整演出来，禁止定格成静帧。"
    "例如升空、翅膀展开、船体转动、扫描光束、仰头等，须在对应时间段内清楚看见。"
    "镜头可随动作轻微上摇、推进或拉远。不要加新台词，不要改变人物、物体、场景和画风。"
)

EXTEND_SYSTEM = """You refine a one-line instruction for extending an existing \
clip. Add only camera movement and pacing so the next seconds continue the \
same shot.

Do not restyle the footage, change characters, props, location, weather, or \
art style, and do not add dialogue or new spoken lines. Keep it to one or two \
sentences. Output only the rewritten instruction."""

# Spoken lines must survive verbatim; Omni's dialogue is already the weak spot.
_DIALOGUE_PATTERNS = (
    re.compile(r"[“\"「『]([^”\"」』\n]{2,})[”\"」』]"),
    re.compile(r"(?:说|問|问|道|喊|答)\s*[:：]\s*([^\n]{2,})"),
)
_IMAGE_TOKEN = re.compile(
    r"(?:@\s*(?:图片|图|image)|(?<![A-Za-z])Image)\s*(\d+)",
    re.IGNORECASE,
)
# Visual facts the rewriter must not introduce unless the author already wrote them.
_LOCKED_VISUAL = re.compile(
    r"清晨|黎明|黄昏|傍晚|正午|中午|夜晚|日出|日落|阴天|雨天|"
    r"dawn|dusk|noon|sunset|sunrise|golden hour|"
    r"沙地|沙尘|荒芜|尘埃|尘土|雾气|金属光泽|高光|"
    r"photoreal|cinematic lighting|volumetric",
    re.IGNORECASE,
)
# The rewrite tends to drop "don't do X" lines; they get restored rather than
# failing the whole pass.
_EXCLUSION_LINE = re.compile(
    r"^\s*(?:不需要|不要|不得|禁止|请勿|no\s|don'?t\s|avoid\s|without\s)",
    re.IGNORECASE,
)


class PromptEnhancementError(RuntimeError):
    pass


def _generate_content_url() -> str:
    project = omni_client._project_id()
    location = settings.prompt_enhance_location or "global"
    host = (
        "aiplatform.googleapis.com"
        if location == "global"
        else f"{location}-aiplatform.googleapis.com"
    )
    return (
        f"https://{host}/v1/projects/{project}/locations/{location}"
        f"/publishers/google/models/{settings.prompt_enhance_model}:generateContent"
    )


def _call_model_sync(system: str, user_text: str) -> str:
    generation_config: dict[str, Any] = {
        "temperature": 0.2,
        "maxOutputTokens": 8192,
        "responseMimeType": "text/plain",
    }
    # Thinking tokens count against maxOutputTokens and truncated the rewrite
    # mid-sentence. This is a rewrite, not a reasoning task.
    if "flash" in settings.prompt_enhance_model:
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}

    payload: dict[str, Any] = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": generation_config,
    }
    session = omni_client._requests_session()
    response = session.post(
        _generate_content_url(),
        headers={
            "Authorization": f"Bearer {omni_client._get_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=(20, settings.prompt_enhance_timeout_sec),
    )
    if response.status_code >= 400:
        raise PromptEnhancementError(
            f"generateContent {response.status_code}: {response.text[:400]}"
        )
    data = response.json()
    candidates = data.get("candidates") or []
    if not candidates:
        raise PromptEnhancementError(f"No candidates: {str(data)[:300]}")
    finish = candidates[0].get("finishReason")
    if finish and finish not in {"STOP", "FINISH_REASON_STOP"}:
        raise PromptEnhancementError(f"finishReason={finish}")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(part.get("text", "") for part in parts).strip()
    if not text:
        raise PromptEnhancementError(f"Empty completion: {str(data)[:300]}")
    return text


SUGGEST_SYSTEM = """You write clickable follow-up instructions that demonstrate \
Gemini Omni Flash video editing and extension, given the shot list that produced \
the clip.

Omni edit (task=edit) is a local visual rewrite of the SAME clip. Official \
examples that work well are short and name one change:
- Remove / make invisible one object already in frame (e.g. "Make the phone \
invisible. Keep everything else the same.")
- Lighting or weather (e.g. "Change the lighting to be more dramatic.")
- Style of the whole clip (e.g. "Make this video anime.")
- Add one simple accessory or prop (e.g. "Put a fashionable hat on this person.")
- Recolor one existing subject (e.g. "Change her jacket to bright red.")
- Change on-screen text if any is described.

Do NOT suggest: rewriting the plot, swapping the location, adding a multi-beat \
action sequence, changing dialogue or voices (voice editing is unsupported), or \
stacking several changes in one instruction. Overly descriptive edits cause \
unintended changes.

Omni extend (task=extend) appends the NEXT few seconds onto the clip. It should \
describe what happens after the last shot, not restate the original. Typical \
continuations:
- The last action continues (walks out of frame, finishes a gesture, looks up).
- Camera continues (slow pull-back, push-in, pan to reveal more of the space).
- Environment evolves (snow denser, lights dim, interface finishes lighting up).
A new beat is allowed only if it follows naturally; do not jump to an unrelated \
scene or invent a second protagonist.

Rules:
- Each instruction is one short English sentence, imperative, under 18 words.
- Cover DISTINCT capability buckets. If you return two edits, they must not both \
be lighting/weather, and not both be recolor.
- Every edit instruction MUST end with "Keep everything else the same."
- Every extend instruction MUST start with "Continue as …" and describe the next \
moment, not a restyle of the existing clip.
- Ground every suggestion in objects, characters, and actions that appear in the \
shot list. Do not name things that are not there. If clothing is not described, \
do not invent a jacket, hat, or similar accessory — pick lighting, weather, \
style, or remove an object that IS in the shot list instead. Address subjects \
by the names used in the shot list, never as a generic "the person".
- Never suggest changing dialogue, audio, music, or voices.
- "label" is a 4–8 character Chinese chip caption."""

_SUGGESTION_SCHEMA = {
    "type": "ARRAY",
    "items": {
        "type": "OBJECT",
        "properties": {
            "mode": {"type": "STRING", "enum": ["edit", "extend"]},
            "instruction": {"type": "STRING"},
            "label": {"type": "STRING"},
        },
        "required": ["mode", "instruction", "label"],
    },
}


def _call_model_json_sync(system: str, user_text: str) -> Any:
    generation_config: dict[str, Any] = {
        "temperature": 0.8,
        "maxOutputTokens": 2048,
        "responseMimeType": "application/json",
        "responseSchema": _SUGGESTION_SCHEMA,
    }
    if "flash" in settings.prompt_enhance_model:
        generation_config["thinkingConfig"] = {"thinkingBudget": 0}

    payload = {
        "systemInstruction": {"parts": [{"text": system}]},
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": generation_config,
    }
    session = omni_client._requests_session()
    response = session.post(
        _generate_content_url(),
        headers={
            "Authorization": f"Bearer {omni_client._get_access_token()}",
            "Content-Type": "application/json; charset=utf-8",
        },
        data=json.dumps(payload),
        timeout=(20, settings.prompt_enhance_timeout_sec),
    )
    if response.status_code >= 400:
        raise PromptEnhancementError(
            f"generateContent {response.status_code}: {response.text[:400]}"
        )
    candidates = response.json().get("candidates") or []
    if not candidates:
        raise PromptEnhancementError("No candidates")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return json.loads("".join(part.get("text", "") for part in parts))


async def suggest_followups(
    scene_prompt: str,
    *,
    allow_edit: bool = True,
    limit: int = 3,
) -> list[dict[str, str]]:
    """Clickable follow-up ideas grounded in the shot list. Never raises."""
    if not scene_prompt.strip():
        return []

    wanted = (
        "Return exactly 3 items: two edits of different kinds (pick from "
        "remove-object, lighting/weather, style, add-accessory, recolor — "
        "never two of the same kind) and one extend that continues the last shot."
        if allow_edit
        else "Return exactly 3 extend suggestions, each a different next beat "
        "(action / camera / environment). Do not return any edit suggestions."
    )
    user_text = f"{wanted}\n\nShot list that produced the clip:\n\n{scene_prompt}"

    try:
        raw = await asyncio.to_thread(
            _call_model_json_sync, SUGGEST_SYSTEM, user_text
        )
    except Exception:
        return []

    if not isinstance(raw, list):
        return []

    out: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        mode = str(item.get("mode", "")).strip().lower()
        instruction = str(item.get("instruction", "")).strip()
        label = str(item.get("label", "")).strip()
        if mode not in {"edit", "extend"} or not instruction:
            continue
        if mode == "edit" and not allow_edit:
            continue
        if mode == "edit" and "keep everything else" not in instruction.lower():
            instruction = instruction.rstrip(".") + ". Keep everything else the same."
        if mode == "extend" and not instruction.lower().startswith("continue"):
            instruction = "Continue as " + instruction[0].lower() + instruction[1:]
        out.append(
            {"mode": mode, "instruction": instruction, "label": label or instruction[:12]}
        )
    return out[:limit]


def _strip_fences(text: str) -> str:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```[a-zA-Z]*\n?", "", stripped)
        stripped = re.sub(r"\n?```$", "", stripped)
    return stripped.strip()


def _dialogue_lines(text: str) -> list[str]:
    found: list[str] = []
    for pattern in _DIALOGUE_PATTERNS:
        for match in pattern.finditer(text):
            line = match.group(1).strip().rstrip("。.!?！？")
            # Quoted reference URLs are not speech.
            if len(line) < 2 or "://" in line:
                continue
            found.append(line)
    return found


def _exclusion_lines(text: str) -> list[str]:
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and _EXCLUSION_LINE.match(line)
    ]


def restore_exclusions(original: str, candidate: str) -> str:
    """Put back any 'do not include X' line the rewrite dropped."""
    missing = [
        line
        for line in _exclusion_lines(original)
        if line.rstrip("。.") not in candidate
    ]
    if not missing:
        return candidate
    return candidate.rstrip() + "\n" + "\n".join(missing)


def validate(original: str, candidate: str) -> tuple[bool, str]:
    """Reject a rewrite that drifted, so we can fall back to the original."""
    if len(candidate) < len(original) * 0.6:
        return False, "rewrite is much shorter than the original"
    if len(candidate) > max(len(original) * 1.8, len(original) + 280):
        return False, "rewrite ballooned past the length budget"

    if set(_IMAGE_TOKEN.findall(original)) != set(_IMAGE_TOKEN.findall(candidate)):
        return False, "image reference tokens changed"

    orig_visual = {m.group(0).lower() for m in _LOCKED_VISUAL.finditer(original)}
    new_visual = {m.group(0).lower() for m in _LOCKED_VISUAL.finditer(candidate)}
    invented = sorted(new_visual - orig_visual)
    if invented:
        return False, "invented look: " + ", ".join(invented[:8])

    for line in _dialogue_lines(original):
        if line not in candidate:
            return False, f"dialogue line was altered: {line[:40]}"

    return True, ""


async def enhance(prompt: str, *, kind: str = "generate") -> dict[str, Any]:
    """Return {"prompt", "enhanced", "reason"}; never raises."""
    result = {"prompt": prompt, "enhanced": False, "reason": ""}
    if not settings.prompt_enhance or not prompt.strip():
        result["reason"] = "disabled"
        return result

    system = EXTEND_SYSTEM if kind == "extend" else GENERATE_SYSTEM
    user_text = prompt
    if kind == "generate":
        user_text = (
            prompt
            + "\n\n[Reminder: do not describe how anyone or anything looks. "
            "Keep every Image N / @图片N token. Add camera moves and transitions only.]"
        )
    try:
        raw = await asyncio.to_thread(_call_model_sync, system, user_text)
    except Exception as exc:
        result["reason"] = f"model call failed: {exc}"
        return result

    candidate = _strip_fences(raw)
    candidate = re.sub(r"\n*\[Reminder:.*?\]\s*$", "", candidate, flags=re.DOTALL).strip()
    ok, reason = validate(prompt, candidate)
    if not ok:
        result["reason"] = f"rejected ({reason})"
        return result

    result["prompt"] = restore_exclusions(prompt, candidate)
    result["enhanced"] = True
    result["reason"] = f"{settings.prompt_enhance_model} ({kind})"
    return result


async def motion_addendum(shot_list: str) -> dict[str, Any]:
    """Append-only motion notes. Never replaces the shot list. Never raises."""
    result = {
        "prompt": DEFAULT_MOTION_NOTE,
        "enhanced": False,
        "reason": "fallback",
    }
    if not settings.prompt_enhance or not shot_list.strip():
        result["reason"] = "disabled"
        return result
    try:
        raw = await asyncio.to_thread(
            _call_model_sync, MOTION_ADDENDUM_SYSTEM, shot_list
        )
    except Exception as exc:
        result["reason"] = f"model call failed: {exc}"
        return result

    candidate = _strip_fences(raw)
    if len(candidate) < 12 or len(candidate) > 900:
        result["reason"] = "rejected (length)"
        return result
    orig_visual = {m.group(0).lower() for m in _LOCKED_VISUAL.finditer(shot_list)}
    new_visual = {m.group(0).lower() for m in _LOCKED_VISUAL.finditer(candidate)}
    if new_visual - orig_visual:
        result["reason"] = "rejected (invented look)"
        return result
    orig_speech = set(_dialogue_lines(shot_list))
    if any(line not in orig_speech for line in _dialogue_lines(candidate)):
        result["reason"] = "rejected (extra dialogue)"
        return result

    result["prompt"] = candidate
    result["enhanced"] = True
    result["reason"] = f"{settings.prompt_enhance_model} (motion addendum)"
    return result
