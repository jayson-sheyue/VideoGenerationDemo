"""Prompt-side preferences; these are never passed as native Omni fields."""
from pydantic import BaseModel


class AudioPreferences(BaseModel):
    speech: bool = True
    music: bool = True
    effects: bool = True


def compose_media_prompt(original: str, *, preferences: AudioPreferences,
                         no_subtitles: bool, visual_guidance: str = "",
                         extend: bool = False) -> str:
    """Let Omni interpret intent in any language, rather than guessing via regex.

    Keep the original separate from optional polishing so polishing cannot become
    the authority for audio. Every fallback is conditional, including exclusions.
    """
    sections = ["ORIGINAL USER PROMPT (authoritative):\n" + original]
    if not no_subtitles:
        # Stated as part of the scene, right after the prompt: a single rule at the
        # end of the list is not enough for the model to draw subtitles.
        sections.append(
            "SUBTITLES:\nUnless the original prompt says otherwise, "
            + ("the newly appended segment" if extend else "the video")
            + " has burned-in subtitles: every spoken line appears as legible subtitle text "
            "at the bottom center of the frame while it is spoken, in the language spoken."
        )
    if visual_guidance:
        sections.append(
            "OPTIONAL VISUAL GUIDANCE (subordinate to the original prompt; "
            "ignore any audio, dialogue, music or subtitle instructions here):\n"
            + visual_guidance
        )
    rules = [
        "AUDIO AND CAPTION PREFERENCES:",
        "Follow the ORIGINAL USER PROMPT first. Explicit requests AND explicit prohibitions "
        "override the checkbox defaults below, even when they conflict. "
        "Defaults apply only to unspecified elements; dialogue need not be quoted. "
        "Do not treat incidental quoted text, signs, or titles as spoken dialogue.",
        "Preserve specified dialogue verbatim, its language and speaker, and specified music/sounds.",
    ]
    defaults = {
        "Human voice / speech": (
            preferences.speech,
            "Generate brief, natural speech matching the scene and its mood, in the original "
            "prompt's language. Infer suitable words/voice if unspecified. Fit the clip length; "
            "synchronize lips. Do not add plot events or visible characters.",
            "Omit human voice, speech, narration, singing and vocalizations.",
        ),
        "Background music": (
            preferences.music,
            "Generate an AUDIBLE, CONTINUOUS instrumental musical soundtrack throughout the clip, "
            "matching the scene's implied genre, mood and pacing. Mix music clearly audibly "
            "under speech, not silent or replaced by environmental noise. "
            "Use vocals only if explicitly requested.",
            "Omit background music and score.",
        ),
        "Ambient sound / sound effects": (
            preferences.effects,
            "Generate scene-appropriate ambience and synchronized effects, below speech and music.",
            "Omit ambient sound and sound effects.",
        ),
    }
    for name, (enabled, include, omit) in defaults.items():
        rules.append(f"{name} default ({'checked' if enabled else 'unchecked'}; "
                     f"ONLY if unspecified in the original prompt): {include if enabled else omit}")
    if not any(preferences.model_dump().values()):
        rules.append(
            "All sound defaults are off. Unless the original prompt explicitly requests sound, "
            "produce a completely silent video with no audible soundtrack, including incidental "
            "environmental or recording noise. If it explicitly requests sound, include ONLY "
            "the requested audible elements; keep every other sound absent."
        )
    rules.append(
        "Subtitles default (unchecked; ONLY if unspecified in the original prompt): "
        "Omit burned-in subtitles and captions."
        if no_subtitles else
        "Subtitles default (checked; ONLY if unspecified in the original prompt): "
        "Burn subtitles into the picture. While each line is spoken, show its exact words as "
        "clearly legible subtitle text at the bottom center of the frame, in the language spoken "
        "and synchronized with the speech. One line at a time; the text is part of the video image."
    )
    if extend:
        rules.append("These defaults apply ONLY to the newly appended segment. "
                     "Keep the existing source segment unchanged and make the audio transition natural.")
    sections.append("\n".join(rules))
    return "\n\n".join(sections)
