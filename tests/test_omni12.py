import unittest

from fastapi.testclient import TestClient

from app.main import app
from app.model_catalog import CATALOG, OMNI_11, OMNI_12, duration_bounds, normalize_labels
from app.omni_client import OmniAPIError, build_payload, format_interaction_failure
from app.prompt_utils import rewrite_video_refs


class PayloadTests(unittest.TestCase):
    def test_text_to_video_keeps_seed_labels_and_fifteen_seconds(self):
        payload = build_payload(
            prompt="a lantern rises",
            duration="15s",
            resolution="720p",
            aspect_ratio="16:9",
            task="text_to_video",
            model=OMNI_12.id,
            seed=7,
            labels={"team": "demo"},
            store=True,
            background=True,
        )
        fmt = payload["response_format"][0]
        self.assertEqual(payload["model"], OMNI_12.id)
        self.assertEqual(fmt["duration"], "15s")
        self.assertEqual(fmt["resolution"], "720p")
        self.assertEqual(payload["generation_config"]["seed"], 7)
        self.assertEqual(payload["labels"], {"team": "demo"})
        self.assertTrue(payload["store"])
        self.assertTrue(payload["background"])
        self.assertEqual([item["type"] for item in payload["input"]], ["text"])

    def test_stateful_edit_does_not_reupload_the_clip(self):
        payload = build_payload(
            prompt="Make the lighting warmer. Keep everything else the same.",
            duration="8s",
            resolution="1080p",
            aspect_ratio="16:9",
            task="edit",
            model=OMNI_12.id,
            video={"mime_type": "video/mp4", "uri": "gs://bucket/clip.mp4"},
            previous_interaction_id="interaction-1",
            store=True,
        )
        fmt = payload["response_format"][0]
        self.assertEqual(payload["previous_interaction_id"], "interaction-1")
        self.assertEqual([item["type"] for item in payload["input"]], ["text"])
        self.assertNotIn("duration", fmt)
        self.assertNotIn("aspect_ratio", fmt)
        self.assertEqual(fmt["resolution"], "1080p")
        self.assertNotIn(
            "video_config",
            payload.get("generation_config") or {},
        )

    def test_extend_sends_duration_only(self):
        # Official Extend sample: duration is the append length; live rejects aspect_ratio.
        payload = build_payload(
            prompt="Continue as she smiles.",
            duration="6s",
            resolution="720p",
            aspect_ratio="9:16",
            task="extend",
            model=OMNI_12.id,
            video={"mime_type": "video/mp4", "uri": "gs://bucket/clip.mp4"},
        )
        fmt = payload["response_format"][0]
        self.assertEqual(fmt["duration"], "6s")
        self.assertNotIn("resolution", fmt)
        self.assertNotIn("aspect_ratio", fmt)
        self.assertEqual(payload["input"][0]["type"], "video")
        self.assertEqual(payload["generation_config"]["video_config"]["task"], "extend")

    def test_extend_keeps_task_and_drops_a_chained_id(self):
        payload = build_payload(
            prompt="Continue as she smiles.",
            duration="6s",
            resolution="720p",
            aspect_ratio="9:16",
            task="extend",
            model=OMNI_12.id,
            video={"mime_type": "video/mp4", "uri": "gs://bucket/clip.mp4"},
            previous_interaction_id="interaction-1",
        )
        self.assertNotIn("previous_interaction_id", payload)
        self.assertEqual(payload["generation_config"]["video_config"]["task"], "extend")
        self.assertEqual(payload["input"][0]["type"], "video")

    def test_extend_without_a_clip_cannot_pretend_to_chain(self):
        with self.assertRaises(OmniAPIError):
            build_payload(
                prompt="Continue as she smiles.",
                duration="6s",
                resolution="720p",
                aspect_ratio="9:16",
                task="extend",
                previous_interaction_id="interaction-1",
            )

    def test_upscale_keeps_task_and_drops_a_chained_id(self):
        payload = build_payload(
            prompt="",
            duration="8s",
            resolution="1080p",
            aspect_ratio="16:9",
            task="upscale",
            model=OMNI_12.id,
            video={"mime_type": "video/mp4", "uri": "gs://bucket/clip.mp4"},
            previous_interaction_id="interaction-1",
        )
        self.assertNotIn("previous_interaction_id", payload)
        self.assertEqual(payload["generation_config"]["video_config"]["task"], "upscale")
        self.assertEqual([item["type"] for item in payload["input"]], ["video"])

    def test_upscale_sends_resolution_and_source_video(self):
        payload = build_payload(
            prompt="Upscale this video.",
            duration="8s",
            resolution="4k",
            aspect_ratio="16:9",
            task="upscale",
            model=OMNI_12.id,
            video={"mime_type": "video/mp4", "uri": "gs://bucket/clip.mp4"},
        )
        fmt = payload["response_format"][0]
        self.assertEqual(payload["generation_config"]["video_config"]["task"], "upscale")
        self.assertEqual(fmt["resolution"], "4k")
        self.assertNotIn("duration", fmt)
        self.assertEqual(payload["input"], [
            {"type": "video", "mime_type": "video/mp4", "uri": "gs://bucket/clip.mp4"},
        ])

    def test_failed_interaction_keeps_the_api_message(self):
        text = format_interaction_failure(
            {
                "status": "failed",
                "errors": [
                    {
                        "code": "prohibited_content",
                        "message": "The input could not be submitted.",
                    }
                ],
            }
        )
        self.assertIn("prohibited_content", text)
        self.assertIn("The input could not be submitted.", text)
        self.assertEqual(format_interaction_failure({"status": "failed"}), "Omni interaction failed")

    def test_examples_name_the_stills_they_attach(self):
        from app.prompt_utils import STILL_FJORD, STILL_KAYAK, STILL_TREE, examples_payload

        payload = examples_payload()["generate"]
        joined = " ".join(
            item["prompt"] for items in payload.values() for item in items
        ).lower()
        self.assertNotIn("fox", joined)
        self.assertNotIn("traveler", joined)
        self.assertNotIn("foliage", joined)
        self.assertNotIn("lantern rises", joined)

        first = payload["image_to_video"]
        self.assertEqual(first[0]["image_urls"], [STILL_FJORD])
        self.assertIn("fjord", first[0]["prompt"].lower())
        self.assertEqual(first[1]["image_urls"], [STILL_KAYAK])
        self.assertIn("kayaker", first[1]["prompt"].lower())
        self.assertIn("white helmet", first[1]["prompt"].lower())

        frames = payload["frames_to_video"]
        self.assertEqual(frames[0]["image_urls"], [STILL_FJORD, STILL_TREE])
        self.assertIn("fjord", frames[0]["prompt"].lower())
        self.assertIn("blossom", frames[0]["prompt"].lower())
        self.assertNotIn("orbit", frames[1]["prompt"].lower())

        refs = payload["reference_to_video"]
        self.assertEqual(refs[0]["image_urls"], [STILL_FJORD, STILL_KAYAK, STILL_TREE])
        for index in range(1, 4):
            self.assertIn(f"@图片{index}", refs[0]["prompt"])
        self.assertEqual(refs[1]["image_urls"], [STILL_FJORD, STILL_TREE])
        self.assertIn("@图片1", refs[1]["prompt"])
        self.assertIn("@图片2", refs[1]["prompt"])
        self.assertNotIn("@图片3", refs[1]["prompt"])

        edits = " ".join(examples_payload()["edit"]).lower()
        self.assertNotIn("coffee", edits)
        self.assertNotIn("jacket", edits)
        extends = " ".join(examples_payload()["extend"]).lower()
        self.assertNotIn("she turns", extends)
        self.assertNotIn("lantern", extends)

    def test_reference_image_can_be_a_url(self):
        from pydantic import ValidationError

        from app.main import ReferenceImage

        img = ReferenceImage(url="https://www.gstatic.com/webp/gallery/1.jpg")
        self.assertEqual(img.url, "https://www.gstatic.com/webp/gallery/1.jpg")
        self.assertIsNone(img.data)
        with self.assertRaises(ValidationError):
            ReferenceImage()

    def test_upscale_result_is_not_chained(self):
        from app.jobs import followup_chain_id, interaction_can_chain

        self.assertFalse(interaction_can_chain({"mode": "upscale", "api_task": "upscale"}))
        self.assertTrue(interaction_can_chain({"mode": "edit", "api_task": "edit"}))
        self.assertTrue(interaction_can_chain({"mode": "generate", "api_task": "text_to_video"}))
        common = dict(
            stateful=True,
            supports_multiturn=True,
            source_interaction_id="interaction-1",
            source_meta={"mode": "generate", "api_task": "text_to_video"},
            source_model=OMNI_12.id,
            requested_model=OMNI_12.id,
            requested_video_uri=None,
            source_video_uri="gs://bucket/clip.mp4",
        )
        self.assertEqual(followup_chain_id(mode="edit", **common), "interaction-1")
        self.assertIsNone(followup_chain_id(mode="extend", **common))
        self.assertIsNone(followup_chain_id(mode="upscale", **common))
        self.assertIsNone(
            followup_chain_id(
                mode="edit",
                **{**common, "requested_video_uri": "gs://bucket/other.mp4"},
            )
        )
        self.assertIsNone(
            followup_chain_id(
                mode="edit",
                **{**common, "source_meta": {"mode": "upscale", "api_task": "upscale"}},
            )
        )

    def test_upscale_ladder_and_track_dimensions(self):
        from app.gcs_util import mp4_track_dimensions
        from app.model_catalog import resolution_label, upscale_targets

        self.assertEqual(upscale_targets("360p"), ("720p", "1080p"))
        self.assertEqual(upscale_targets("720p"), ("1080p", "4k"))
        self.assertEqual(upscale_targets("1080p"), ())
        self.assertIsNone(upscale_targets(None))
        self.assertEqual(resolution_label(720, 1280), "720p")
        self.assertEqual(resolution_label(1080, 1920), "1080p")

        width = (1280 * 65536).to_bytes(4, "big")
        height = (720 * 65536).to_bytes(4, "big")
        header = b"\x00\x00\x00\x5c" + b"tkhd" + b"\x00\x00\x00\x00" + (b"\x00" * 72) + width + height
        self.assertEqual(mp4_track_dimensions(header), (1280, 720))

    def test_extend_input_differs_between_models(self):
        self.assertIsNone(OMNI_12.extend_input_max)
        self.assertEqual(OMNI_11.extend_input_max, 30)
        flash = next(p for p in CATALOG.values() if p.id == OMNI_12.id).to_public()
        preview = next(p for p in CATALOG.values() if p.id == OMNI_11.id).to_public()
        self.assertIsNone(flash["extend_input_max"])
        self.assertEqual(preview["extend_input_max"], 30)
        self.assertNotIn("extend_live_source_max", flash)

    def test_reference_videos_precede_the_prompt(self):
        payload = build_payload(
            prompt="the person in <VIDEO_REF_0> walks",
            images=[{"mime_type": "image/png", "uri": "gs://bucket/a.png"}],
            videos=[{"mime_type": "video/mp4", "uri": "gs://bucket/ref.mp4"}],
            duration="8s",
            resolution="720p",
            aspect_ratio="16:9",
            task="reference_to_video",
            model=OMNI_11.id,
        )
        self.assertEqual(
            [item["type"] for item in payload["input"]],
            ["video", "image", "text"],
        )
        self.assertNotIn("seed", payload["generation_config"])
        self.assertNotIn("store", payload)

    def test_video_tag_rewrite_and_duration_bounds(self):
        self.assertIn("<VIDEO_REF_0>", rewrite_video_refs("看 @视频1"))
        self.assertEqual(duration_bounds(OMNI_12, mode="generate", generate_task="text_to_video"), (3, 15))
        self.assertEqual(duration_bounds(OMNI_12, mode="generate", generate_task="frames_to_video"), (3, 10))
        self.assertEqual(duration_bounds(OMNI_11, mode="extend", generate_task=None), (3, 10))
        self.assertEqual(normalize_labels({"Team": "Demo"}), {"team": "demo"})
        with self.assertRaises(ValueError):
            normalize_labels({"Bad Key": "x"})

    def test_fragmented_mp4_needs_remux(self):
        from app.gcs_util import mp4_is_fragmented, mp4_needs_remux, mvhd_duration_sec

        # Minimal fMP4-shaped head: mvhd duration 0 + mvex + moof.
        head = (
            b"\x00\x00\x00\x18ftypiso5\x00\x00\x02\x00iso5"
            + b"\x00\x00\x00\x6cmvhd"
            + b"\x00"  # version
            + b"\x00\x00\x00"  # flags
            + b"\x00" * 8  # creation/modification
            + b"\x00\x00\x03\xe8"  # timescale 1000
            + b"\x00\x00\x00\x00"  # duration 0
            + b"\x00" * 76
            + b"\x00\x00\x00\x08mvex"
            + b"\x00\x00\x00\x08moof"
        )
        self.assertIsNone(mvhd_duration_sec(head))
        self.assertTrue(mp4_is_fragmented(head))
        self.assertTrue(mp4_needs_remux(head))

        # Progressive: non-zero mvhd duration, no fragments.
        progressive = (
            b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isom"
            + b"\x00\x00\x00\x6cmvhd"
            + b"\x00"
            + b"\x00\x00\x00"
            + b"\x00" * 8
            + b"\x00\x00\x03\xe8"
            + b"\x00\x00\x27\x10"  # duration 10000 @ 1000 = 10s
            + b"\x00" * 76
        )
        self.assertAlmostEqual(mvhd_duration_sec(progressive) or 0, 10.0)
        self.assertFalse(mp4_is_fragmented(progressive))
        self.assertFalse(mp4_needs_remux(progressive))

    def test_remux_to_progressive_restores_mvhd_duration(self):
        """Lossless remux must leave a readable non-zero mvhd (Omni source gate)."""
        import subprocess
        import tempfile
        from pathlib import Path

        import imageio_ffmpeg

        from app.gcs_util import (
            mp4_is_fragmented,
            mp4_needs_remux,
            mvhd_duration_sec,
            remux_to_progressive_mp4,
        )

        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory() as tmp:
            progressive = Path(tmp) / "progressive.mp4"
            frag = Path(tmp) / "frag.mp4"
            out = Path(tmp) / "out.mp4"
            # Build a tiny clip, then force fragmented packaging (mvex/moof, often duration 0).
            make = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    "color=c=black:s=160x160:d=1",
                    "-c:v",
                    "libx264",
                    "-pix_fmt",
                    "yuv420p",
                    str(progressive),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(make.returncode, 0, make.stderr)
            frag_cmd = subprocess.run(
                [
                    ffmpeg,
                    "-y",
                    "-hide_banner",
                    "-loglevel",
                    "error",
                    "-i",
                    str(progressive),
                    "-c",
                    "copy",
                    "-movflags",
                    "frag_keyframe+empty_moov+default_base_moof",
                    str(frag),
                ],
                capture_output=True,
                text=True,
            )
            self.assertEqual(frag_cmd.returncode, 0, frag_cmd.stderr)
            head = frag.read_bytes()[:256 * 1024]
            self.assertTrue(mp4_needs_remux(head), "fixture must look like stock fMP4")
            remux_to_progressive_mp4(frag, out)
            remuxed = out.read_bytes()
            duration = mvhd_duration_sec(remuxed[:65536])
            self.assertIsNotNone(duration)
            self.assertGreater(duration or 0, 0.5)
            self.assertFalse(mp4_is_fragmented(remuxed[:256 * 1024]))
            self.assertFalse(mp4_needs_remux(remuxed[:65536]))
            self.assertFalse(mp4_needs_remux(progressive.read_bytes()[:65536]))


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(app)

    def test_docs_page_links_the_model_card(self):
        response = self.client.get("/docs")
        self.assertEqual(response.status_code, 200)
        text = response.text
        self.assertIn("gemini-omni-1.2-flash", text)
        self.assertIn("https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-2-flash", text)
        self.assertIn("previous_interaction_id", text)

    def test_upscale_rejects_an_illegal_target_before_submit(self):
        from app.jobs import Job, JobStatus, job_store

        job = Job(
            id="src360",
            status=JobStatus.completed,
            video_uri="gs://bucket/clip.mp4",
            interaction_id="interaction-1",
            duration_sec=4,
        )
        job.meta = {
            "output_resolution": "360p",
            "model": OMNI_12.id,
            "mode": "generate",
            "api_task": "text_to_video",
        }
        job_store._jobs[job.id] = job
        try:
            response = self.client.post(
                "/api/generate",
                json={
                    "mode": "upscale",
                    "prompt": "",
                    "source_job_id": job.id,
                    "resolution": "4k",
                    "model": OMNI_12.id,
                    "stateful": True,
                },
            )
            self.assertEqual(response.status_code, 400)
            self.assertIn("360p", response.json()["detail"])
        finally:
            job_store._jobs.pop(job.id, None)

    def test_health_lists_both_models(self):
        body = self.client.get("/api/health").json()
        ids = {item["id"] for item in body["models"]}
        self.assertIn(OMNI_12.id, ids)
        self.assertIn(OMNI_11.id, ids)
        flash = next(item for item in body["models"] if item["id"] == OMNI_12.id)
        self.assertEqual(flash["generate_max"], 15)
        self.assertTrue(flash["supports_upscale"])
        self.assertTrue(flash["supports_multiturn"])

    def test_preview_rejects_fifteen_seconds_seed_and_upscale(self):
        common = {
            "prompt": "a lantern",
            "model": OMNI_11.id,
            "generate_task": "text_to_video",
        }
        too_long = self.client.post("/api/generate", json={**common, "duration": 12})
        self.assertEqual(too_long.status_code, 400)
        seeded = self.client.post("/api/generate", json={**common, "duration": 8, "seed": 1})
        self.assertEqual(seeded.status_code, 400)
        upscale = self.client.post(
            "/api/generate",
            json={"prompt": "upscale", "model": OMNI_11.id, "mode": "upscale", "source_video_uri": "gs://b/a.mp4"},
        )
        self.assertEqual(upscale.status_code, 400)

    def test_frames_on_12_stop_at_ten_seconds(self):
        response = self.client.post(
            "/api/generate",
            json={
                "prompt": "move between the frames",
                "model": OMNI_12.id,
                "mode": "generate",
                "generate_task": "frames_to_video",
                "duration": 12,
                "reference_images": [
                    {"mime_type": "image/png", "data": "abcdefgh"},
                    {"mime_type": "image/png", "data": "ijklmnop"},
                ],
            },
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("10", response.json()["detail"])

    def test_index_exposes_upscale_and_docs(self):
        html = self.client.get("/").text
        self.assertIn('id="capabilities"', html)
        self.assertIn('id: "upscale"', self.client.get('/static/app.js').text)
        self.assertIn('data-view="reference"', html)
        self.assertIn("gemini-omni-1.2-flash", html)


if __name__ == "__main__":
    unittest.main()
