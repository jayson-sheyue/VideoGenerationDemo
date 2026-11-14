import itertools
import unittest
from unittest.mock import AsyncMock, patch

from app.audio_direction import AudioPreferences, compose_media_prompt
from app.jobs import Job, run_generation_job
from app.main import GenerateRequest
from app.model_catalog import OMNI_12


class AudioPromptTests(unittest.TestCase):
    def test_original_requirements_survive_every_checkbox_combination(self):
        prompts = [
            '她说：“今天去看海。”配轻快钢琴曲。',
            'Have the guide explain the garden in English, with jazz music.',
            '静かな庭。台詞も音楽も不要。',
            'A shop sign says "Hello". Silent film, no audio, no captions.',
        ]
        for prompt, flags, subtitles in itertools.product(prompts, itertools.product([False, True], repeat=3), [False, True]):
            with self.subTest(prompt=prompt, flags=flags, subtitles=subtitles):
                prefs = AudioPreferences(**dict(zip(['speech', 'music', 'effects'], flags)))
                sent = compose_media_prompt(prompt, preferences=prefs, no_subtitles=subtitles)
                self.assertTrue(sent.startswith('ORIGINAL USER PROMPT (authoritative):\n' + prompt))
                self.assertIn('Explicit requests AND explicit prohibitions override', sent)
                self.assertIn('dialogue need not be quoted', sent)
                self.assertIn('Do not treat incidental quoted text', sent)
                for label, flag in zip(['Human voice / speech', 'Background music', 'Ambient sound / sound effects'], flags):
                    self.assertIn(f"{label} default ({'checked' if flag else 'unchecked'}; ONLY if unspecified", sent)
                self.assertNotIn('No dialogue. No speech', sent)
                self.assertNotIn('No background music.', sent)
                self.assertEqual('All sound defaults are off.' in sent, not any(flags))

    def test_subtitles_are_stated_with_the_scene_when_ticked_and_omitted_otherwise(self):
        prompt = '她说：“我们出发吧。”'
        on = compose_media_prompt(prompt, preferences=AudioPreferences(), no_subtitles=False,
                                  visual_guidance='Slow camera move.')
        # The scene-level statement sits between the prompt and any polishing notes.
        self.assertLess(on.index(prompt), on.index('SUBTITLES:\nUnless the original prompt says otherwise, the video has burned-in subtitles'))
        self.assertLess(on.index('SUBTITLES:'), on.index('OPTIONAL VISUAL GUIDANCE'))
        self.assertIn('Subtitles default (checked; ONLY if unspecified in the original prompt): Burn subtitles into the picture.', on)
        self.assertNotIn('Omit burned-in subtitles', on)

        off = compose_media_prompt(prompt, preferences=AudioPreferences(), no_subtitles=True)
        self.assertNotIn('SUBTITLES:', off)
        self.assertNotIn('Burn subtitles', off)
        self.assertIn('Subtitles default (unchecked; ONLY if unspecified in the original prompt): Omit burned-in subtitles and captions.', off)

        more = compose_media_prompt(prompt, preferences=AudioPreferences(), no_subtitles=False, extend=True)
        self.assertIn('the newly appended segment has burned-in subtitles', more)
        self.assertNotIn('the video has burned-in subtitles', more)

    def test_app_preferences_and_legacy_defaults(self):
        body = GenerateRequest(audio_preferences={'speech': False, 'music': True, 'effects': False})
        self.assertFalse(body.audio_preferences.speech)
        self.assertTrue(body.audio_preferences.music)
        self.assertFalse(GenerateRequest().no_bgm)
        self.assertTrue(GenerateRequest(no_bgm=True).no_bgm)


class AudioPipelineTests(unittest.IsolatedAsyncioTestCase):
    async def run_job(self, *, kind='text_to_video', mode='generate', enhance=False, prefs=None):
        job = Job(id='audio-test', owner='local')
        refs = ([{'uri': 'gs://example/first.png', 'mime_type': 'image/png'}]
                if kind in {'image_to_video', 'reference_to_video'} else
                [{'uri': 'gs://example/first.png'}, {'uri': 'gs://example/last.png'}]
                if kind == 'frames_to_video' else [])
        with patch('app.jobs._submit_and_finalize', new_callable=AsyncMock) as submit, \
             patch('app.jobs.job_store.save', new_callable=AsyncMock), \
             patch('app.jobs.ensure_omni_source_mp4', return_value=('gs://example/source.mp4', 4, {})), \
             patch('app.jobs.probe_video_resolution', return_value='360p'), \
             patch('app.jobs.prompt_enhancer.enhance', new_callable=AsyncMock,
                   return_value={'enhanced': True, 'reason': 'test', 'prompt': 'Warm lighting. No background music.'}), \
             patch('app.jobs.prompt_enhancer.motion_addendum', new_callable=AsyncMock,
                   return_value={'enhanced': True, 'reason': 'test', 'prompt': 'Slow camera move.'}):
            await run_generation_job(job, prompt='她说：“我们出发吧。”背景是钢琴音乐。', image_urls=[],
                                     duration=5, resolution='720p', aspect_ratio='16:9',
                                     generate_audio=True, no_subtitles=True, no_bgm=True,
                                     audio_preferences=prefs, mode=mode, generate_task=kind,
                                     enhance_prompt=enhance, reuse_images=refs, model=OMNI_12.id,
                                     source_video_uri='gs://example/source.mp4' if mode != 'generate' else None)
            self.assertIsNone(job.error)
            submit.assert_awaited_once()
            return job, submit.call_args.kwargs['payload']

    async def test_four_generation_modes_all_audio_combinations_with_and_without_polishing(self):
        for kind, flags, enhance in itertools.product(
            ['text_to_video', 'image_to_video', 'frames_to_video', 'reference_to_video'],
            itertools.product([False, True], repeat=3), [False, True],
        ):
            with self.subTest(kind=kind, flags=flags, enhance=enhance):
                prefs = AudioPreferences(**dict(zip(['speech', 'music', 'effects'], flags)))
                job, payload = await self.run_job(kind=kind, enhance=enhance, prefs=prefs)
                sent = next(x['text'] for x in payload['input'] if x['type'] == 'text')
                self.assertEqual(sent, job.meta['final_prompt_preview'])
                self.assertEqual(job.meta['audio_preferences'], prefs.model_dump())
                self.assertIn('AUDIO AND CAPTION PREFERENCES:', sent)
                if enhance:
                    self.assertIn('ignore any audio, dialogue, music or subtitle instructions here', sent)
                self.assertNotIn('audio_preferences', payload)
                self.assertNotIn('generate_audio', payload.get('generation_config', {}))
                self.assertNotIn('No dialogue. No speech', sent)

    async def test_extend_only_new_segment_edit_unmodified_upscale_no_text(self):
        for mode, enhance in itertools.product(['extend', 'edit', 'continue', 'upscale'], [False, True]):
            with self.subTest(mode=mode, enhance=enhance):
                job, payload = await self.run_job(mode=mode, enhance=enhance, prefs=AudioPreferences())
                texts = [x['text'] for x in payload['input'] if x['type'] == 'text']
                if mode == 'extend':
                    self.assertIn('ONLY to the newly appended segment', texts[0])
                elif mode == 'upscale':
                    self.assertEqual(texts, [])
                else:
                    self.assertNotIn('AUDIO AND CAPTION PREFERENCES', texts[0])
                    self.assertIsNone(job.meta['audio_preferences'])

    async def test_legacy_bgm_false_maps_to_new_policy(self):
        job, _ = await self.run_job()
        self.assertEqual(job.meta['audio_preferences'], {'speech': True, 'music': False, 'effects': True})


if __name__ == '__main__':
    unittest.main()
