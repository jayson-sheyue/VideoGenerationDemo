# Gemini Omni Flash 学习指南

本文说明 **Omni 1.2 Flash（GA）** 和 **Omni 1.1 Flash（Preview）** 能做什么、请求怎么组、参数上限是什么，以及和本仓库 Demo 的差别。规格以 Google Cloud 文档为准（核对于 2026-11-06），并保留本 Demo 对 **线上 API** 的实测修正。运行中的汇总页是 `/docs`。

官方入口：

- 模型卡片：[Gemini Omni 1.2 Flash](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-2-flash) · [1.1 Flash Preview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash)
- 视频总览：[Generate videos with Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/overview)
- Interactions API：[locations/global/interactions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/interactions-api)
- 发布说明：[Introducing Gemini Omni 1.2 Flash](https://blog.google/innovation-and-ai/technology/developers-tools/introducing-gemini-omni-1-2-flash/) · [Cloud GA 公告](https://cloud.google.com/blog/products/ai-machine-learning/gemini-omni-1-2-flash-generally-available)
- Gemini API 侧的提示词和限制：[Generate and edit videos](https://ai.google.dev/gemini-api/docs/omni)

## 0. 1.2 相对 1.1 多出来的部分

模型 ID：`gemini-omni-1.2-flash`。1.1 仍是 `gemini-omni-1.1-flash-preview`。

| | 1.2 | 1.1 |
| --- | --- | --- |
| 生成 | 3–15 秒。首尾帧 3–10 秒 | 3–10 秒 |
| 编辑输入 | ≤ 15 秒，可再加最多 2 张参考图 | ≤ 10 秒（线上实测 `Editing duration N exceeds maximum duration 10`） |
| 多轮 | `store: true` + `previous_interaction_id`。状态约 7 天 | Agent Platform 会拒 `previous_interaction_id`，只能重传 `gs://` |
| 续写输入 | **任意长度**（模型卡片：`extension: Any duration`） | 本 Demo **仍拦截 > 30 秒** 的源片（保守上限；1.1 卡片未单独写 Any duration） |
| 续写怎么用源片 | **看约 10 秒**（上下文）≠ **留约 30 秒**（成片保留窗）。模型只看最后约 10 秒来演下一段；输出文件只保留源片最后约 30 秒再接新段，更早画面裁掉 | 单次同样 3–10 秒；成片同样约 ≤ 40 秒 |
| 续写输出 | 新段 3–10 秒 + 源片末尾约 30 秒 ≈ 成片 ≤ **40 秒** | 同上 |
| 放大 | `task=upscale`，源片 ≤ 15 秒。只传源视频和目标分辨率，线上拒绝文字 | 不发送 |
| Seed / labels | `generation_config.seed`（0–4294967295）；请求级 `labels` | 不发送 |
| 参考视频 | 每条 prompt 最多 3 段。Gemini API 建议每段约 3 秒，声音会被忽略 | 文档同样列出，本 Demo 两种模型都可以挂 |
| 音频输入 | 模型卡片：不支持。Cloud 博客：allowlist | 不支持 |
| 分辨率 | 360p / 720p / 1080p / 4k。低分辨率适合先出草稿再放大 | 同样四档 |
| 价格（发布说明） | 720p 约 $0.07/秒，比 1.1 低约 30% | 见当时的价目 |

1.2 请求在本 Demo 里还会带 `background: true`，创建调用尽快返回 id，页面继续轮询。取消等待不会取消已经提交的生成。

下面第 3 节起的 task 说明仍然适用。凡是写「10 秒」的编辑上限，在 1.2 上改读 **15 秒**；文生 / 首帧 / 参考的时长上限改读 **15 秒**。首尾帧不要超过 10 秒。

---

## 1. 它是什么

Omni 1.1 Flash（Preview，模型 ID `gemini-omni-1.1-flash-preview`）是面向 **视频生成与编辑** 的多模态模型：一次 Interaction 里可以吃文本、图片、视频，吐出视频（以及模型 thoughts 文本）。音频 **不能作为输入模态**，但 **成片可以带对白、音效、音乐**。

和常见 Gemini 文本模型不同：

| | Omni Flash | 普通 Gemini（如 2.5 Flash） | Veo |
| --- | --- | --- | --- |
| 调用方式 | **Interactions API** `…/locations/global/interactions` | `generateContent` | `predict` / `generate_videos` |
| 主产出 | 视频 | 文本 / 图 | 视频 |
| 任务开关 | `generation_config.video_config.task` | 无此字段 | 另一套 instance/params |
| 系统指令 | **不支持** | 支持 | 无同等概念 |
| 区域 | **仅 `global`** | 多区域 | 多区域 |

Preview 阶段：有固定配额，没有 Batch / Provisioned Throughput。输出带 **C2PA / SynthID** 内容凭证。

### 模型卡片要点（1.1）

- 输入最多约 131k tokens，输出约 58k tokens。
- 图片：每条 prompt 最多 **10** 张；inline 单文件 20 MB，GCS 单文件 30 MB；`png` / `jpeg` / `webp` / `heic` / `heif`。
- 视频输入：每条 prompt 最多 **3** 段；卡片上的「单段最长 10s」主要约束 **编辑类** 输入。续写见第 3.5 节：1.2 输入可为任意长度，成片约 ≤ 40s。
- 成片画幅 **16:9 / 9:16**；分辨率 **360p / 720p / 1080p / 4k**（上一代 `gemini-omni-flash-preview` 基本只有 720p）。
- 能力开关：文生视频、图生视频、参考生视频、首尾帧、配音/音效、编辑、续写 —— 均为 Supported。不支持：生图、改图、Function calling、URL context、Chat completions。

上一代 `gemini-omni-flash-preview` **没有** 参考生视频、首尾帧、续写；新项目请用 **1.1**。

---

## 2. Interactions 请求长什么样

所有视频任务都是：

```http
POST https://aiplatform.googleapis.com/v1beta1/projects/PROJECT_ID/locations/global/interactions
```

骨架：

```json
{
  "model": "gemini-omni-1.1-flash-preview",
  "input": [
    { "type": "video", "uri": "gs://…/source.mp4", "mime_type": "video/mp4" },
    { "type": "image", "uri": "gs://…/char.png", "mime_type": "image/png" },
    { "type": "text", "text": "你的提示词" }
  ],
  "response_format": [
    {
      "type": "video",
      "aspect_ratio": "16:9",
      "resolution": "720p",
      "duration": "8s",
      "delivery": "uri",
      "gcs_uri": "gs://your-bucket/omni-output/"
    }
  ],
  "generation_config": {
    "video_config": { "task": "reference_to_video" }
  }
}
```

要点：

1. **`input` 顺序**：媒体（视频、图）放在文本前面更稳。本 Demo 按「源视频 → 参考图 → 文本」组装。
2. 媒体必须是 **`uri`（GCS）或 `data`（base64）**，不能是 `https://` 热链。
3. `delivery: "uri"` + `gcs_uri` 让成片落到 bucket；省略则响应里带 base64，大视频容易撑爆。
4. 同步请求会等到成片（常超过一分钟）。异步则在某个 input 项上设 `"background": true`，拿返回的 `id` 再 GET/POST 同一条 interaction；异步结果最多保留约 14 天。
5. 响应在 `steps` 里：`thought` 是模型自我解释，`model_output` 里 `type: video` 带 `uri` 或 `data`。

本 Demo 轮询 `GET …/interactions/{id}`；个别环境对 GET 返回 405 时会改用空 body 的 POST。

---

## 3. 五个 `task`（官方能力）

`generation_config.video_config.task` 决定语义。不写的话，实现可以按「有视频 → edit、有图 → reference_to_video、否则 text_to_video」推断，但 **显式写出更安全**。

### 3.1 `text_to_video` — 纯文生视频

只给文本。适合没有角色一致性要求的镜头。提示词里写清：主体、动作、镜头（焦距/机位）、光线、风格、对白。

成片参数（`response_format`）三者都生效：`aspect_ratio`、`resolution`、`duration`（`3s`–`10s`）。

本 Demo：Generate → **Text → video**。不需要图；「试用示例」里有庭院灯笼、竖屏夜市、微距水滴。

文档：[Generate videos from text](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-text-prompts)（路径以控制台「What's next」为准）。

### 3.2 `image_to_video` — 图作**第一帧**（可选最后一帧）

把图片当作 **字面意义上的起始画面**，而不是「长得像这张图的角色」。适合运镜、转场、loop。

- 一张图：从该帧开始演。Demo 里是 **First frame**。
- 两张图（首 + 尾）：在两帧之间插值，适合环绕、推拉、无缝循环。Demo 里是 **First + last**（API 仍是 `image_to_video`，只是必须两张、顺序为先首后尾）。见 [first and last frames](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-first-and-last-frames)。

提示词只写 **从这一帧怎么动**，不要重写人物长相。图可以本地上传或贴公网 URL。

文档：[Generate videos from an image](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-an-image)。

### 3.3 `reference_to_video` — 参考图 / 参考视频「像它」，不当首帧

参考图提供人物、服装、场景、画风；模型应 **仿其外观去演分镜**，而不是把图贴成第一帧。官方提示里常带：

> Use the given image(s) as references for video generation. The images should not be used as literal initial frames.

也可以同时挂最多 **3** 段参考视频（人物、动作或风格）。本 Demo 的 References 模式可以上传或粘贴 `gs://`。提示词里的 `@视频N` 会改成 `<VIDEO_REF_N>`。参考视频里的声音会被忽略，不要把它当剪辑源。

绑定方式（本 Demo 按官方 cookbook 做的）：

1. 提示词顶部：`[# References <IMAGE_REF_0>@Image1 <IMAGE_REF_1>@Image2 …]`
2. 分镜里用 `<IMAGE_REF_0>` 指第一张图。用户写 `@图片1` / `Image 1` 时，服务端会改成 0-based 标签。
3. 不要在文本里再散文式重写五官和画风，否则文本会 **盖过** 参考图。

本 Demo：Generate → **References**。用 `@图片1` 绑定；「试用示例」会填入公开静帧，可再换成自己的上传。

文档：[Generate videos from references](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-references)。

Veo 的 reference 还有 `asset` / `style` 类型且张数更少；Omni 走 Interactions 的 image 列表，张数上限以模型卡片的 10 为准。

### 3.4 `edit` — 改已有视频（局部、同长度）

输入：源视频 `gs://` + **一句短指令**。输出仍是同一段时间轴上的新成片（画幅、时长跟源片），不是在后面接戏。

适合：

- 去掉画面里已有的物体（「Make the phone invisible.」）
- 改光 / 天气（「更戏剧化的灯光」）
- 整段换风格（「变成动漫」）
- 加一件简单配饰、给已有主体换色
- 改画面上已有的字（如果有）

不适合：改剧情、换场景、加多拍动作、改对白或配音。官方强调 **写多了会改到你没点名的地方**。稳妥写法：只点一件事，并以 `Keep everything else the same.` 收尾。

硬限制（线上实测）：

- 源片 **1.2 ≤ 15 秒，1.1 ≤ 10 秒**。超限会报 `Editing duration N exceeds maximum duration …`。
- `response_format` **只接受 `resolution`**（外加可选的 `delivery` / `gcs_uri`）。传 `aspect_ratio` 或 `duration` 会被拒。
- 当前 **不能改声音 / 台词**。

文档：[Edit videos](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/edit-videos)。

多轮只用于 **Continue / Edit**。1.2 把上一次 `interaction.id` 放进 `previous_interaction_id`（需要 `store: true`），**并且不能再带** `generation_config.video_config.task`。线上会直接 400：`previous_interaction_id is not allowed when video task is set`。这一轮只发新指令，不重传成片。**续写和放大不能走这条链**：它们必须带 `task=extend` / `task=upscale`，所以始终重传 `gs://` 成片，不带 `previous_interaction_id`。放大文档里的「上一轮 id + task=upscale」和这条线上错误冲突，本 Demo 用已经跑通的重传路径。**1.1 在 Agent Platform 上会拒这个字段**，所以 1.1 的 Continue 仍是新请求，把最新成片的 `gs://` 再当 `input.video`，并带 `task=edit`。1.2 取消「多轮编辑」，或源片地址和任务成片不一致时，走同一条重传路径。1.2 的编辑还可以再带最多两张参考图。

### 3.5 `extend` — 续写（往后接，返回整段）

输入：源视频 + 「接下来几秒发生什么」。模型从片尾接着演，并尽量保持人物、场景、画风。

这是 **1.1 与 1.2 最容易讲错的差别之一**。官方依据：

- [Extend videos](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos)：Gemini Omni 可接受 **任意长度** 源片。官方把两步写在一句里，容易读拧——实际是：
  1. **上下文窗（约 10 秒）**：模型只看源片最后约 10 秒，用来决定「接下来怎么演」，并生成 3–10 秒新段。
  2. **成片保留窗（约 30 秒）**：返回的文件不是「整段源片 + 新段」，而是「源片最后约 30 秒 + 新段」。源片更前面的部分会被裁掉。
  所以 60 秒源片再续 10 秒 → 约 **40 秒** 成片（末 30 + 新 10），不是 70 秒。
- [1.2 模型卡片](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-2-flash)：`Maximum input video length (extension): Any duration (uses up to the last 10 seconds)`——括号里的 10 秒指的是**上下文**，不是成片只留 10 秒。
- [1.1 模型卡片](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash)：已 Deprecated，**没有**单独写 `extension: Any duration`。本 Demo 对 1.1 仍按 **源片 ≤ 30 秒** 拦截。

| | Omni 1.2 Flash | Omni 1.1（本 Demo） | Veo（对照） |
| --- | --- | --- | --- |
| 源片长度 | **任意长度** | **≤ 30 秒**（本 Demo 保守上限） | 1–30 秒，常要求 24fps MP4 |
| 上下文 | 源片最后约 **10 秒** | 同左（本 Demo 输入先被 30s 拦住） | — |
| 成片里保留的源片 | 源片最后约 **30 秒** | 同左 | — |
| 单次 `duration` | **3–10 秒**（本次新增） | 同样 3–10 秒 | 常约 7 秒 |
| 成片总长 | **约 ≤ 40 秒**（≈ 末 30 + 新 ≤10） | 约 ≤ 40 秒 | 约 37 秒 |

返回的是 **拼接后的完整视频**：8s 源片再续 8s → 约 **16s**，不是只返回新的 8s。因此：

- 超过当前模型编辑上限后再 Edit 会失败（1.2 为 15s，1.1 为 10s）
- 还可以继续 Extend，直到接近约 40s；再续也不会稳定涨到 45s
- **不能**按「15 + 15 + 15」理解：单次续写最多 10 秒，不是 15 秒

`response_format` 按官方 Extend 示例传 **`duration`（本次追加 3–10 秒）**。文档里的 `aspect_ratio` 线上会拒，本 Demo 不传。分辨率沿用源片，不在 extend 请求里再塞 `resolution`。

提示词应写 **片尾之后** 的动作/运镜/环境变化，不要把原片再描述一遍，也不要新开无关场景或第二个主角。

**源片**：官方合同为 **任意长度**。本 Demo 按该合同提交，不裁短源片。仅对素材站常见的 fragmented / CMAF（`mvhd` 时长为 0）做 progressive remux（容器卫生，不改变片长）。

---

## 4. `response_format` 对照表（务必按 task 裁剪）

API 对「这个 task 不该出现的字段」是直接 4xx，而不是忽略。

| `task` | `aspect_ratio` | `resolution` | `duration` | `delivery` / `gcs_uri` |
| --- | --- | --- | --- | --- |
| `text_to_video` / `image_to_video` / `reference_to_video` | ✓ | ✓ | ✓（1.2 为 3s–15s；首尾帧 3s–10s；1.1 为 3s–10s） | 推荐 |
| `edit` | ✕ | ✓ | ✕（跟源片） | 推荐 |
| `upscale`（仅 1.2） | ✕ | ✓（目标分辨率） | ✕ | 推荐 |
| `extend` | ✕（文档有、线上拒） | ✕ | ✓（本次续写 3s–10s） | 推荐 |

画幅取值：`16:9`、`9:16`。分辨率：`360p`、`720p`、`1080p`、`4k`。时长字符串必须带 `s`，例如 `"8s"`。

### 放大，以及它和 Transcoder API 的差别

`upscale` 只提高分辨率。[VideoGenerationModelParams](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/rest/Shared.Types/VideoGenerationModelParams) 的原文是 “Upscale a video to a higher resolution”。[1.2 模型卡片](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-2-flash) 把它列为 standalone video upscaling，输入最长 15 秒。[GA 公告](https://cloud.google.com/blog/products/ai-machine-learning/gemini-omni-1-2-flash-generally-available) 建议先用 360p / 720p 出草稿，再放大到 1080p 或 4K。

线上实测：input 里只要有 `type: text`，就返回 `Text input is not supported for upscale task`。本 Demo 因此不传提示词、参考图或 `previous_interaction_id`，只传源视频和 `response_format.resolution`。不能用它改内容。目标分辨率必须高于源片，且遵守阶梯：360p → 720p 或 1080p；720p → 1080p 或 4k；**1080p → 4k 不支持**。知道源分辨率时，Demo 会在提交前拦住非法组合。

[Cloud Transcoder API](https://docs.cloud.google.com/transcoder/docs/concepts/overview) 是转码服务：容器（MP4 / DASH / HLS）、码率、裁剪、贴片、编码参数、HDR、加密、缩略图。它的分辨率变化是重采样后再编码，不是 Omni 的生成式放大，也不受 15 秒上限约束。本 Demo 不调用它。

---

## 5. 声音与对白

- Omni **没有** Veo 的独立 `generate_audio` 开关。有没有人声、BGM、字幕，主要靠 **提示词**。
- 模型卡片：支持 speech / music / sound effects；**不支持把音频当输入**。
- 实践经验（本 Demo）：
  - 分镜里用引号写出要说的那一句，并加锁：「只允许这些台词，不要翻译、不要即兴」。
  - 没有台词时写明：无对白、无旁白，只要环境声。
  - **不要**写 `Generate dialogue` 这类总开关，容易多出解说。
  - `no burned-in subtitles`、`no background music` 写在提示词里（本 Demo 的勾选会拼进 control prefix）。
- **Edit 改不了配音。** 要换台词只能重新 Generate。

---

## 6. 提示词怎么写才跟得上模型

Omni 对提示词 **跟得紧，但自己很少补电影感**。分镜太干，结果容易像「会动的静帧」；分镜写成小说，又容易和参考图打架。

### 生成（有参考图）

1. 一行技术规格：时长、画幅、风格（「illustrated look」等，且不要和参考图矛盾）。
2. 角色 / 场景用 `@图片N` 绑定，**不要再描写五官、服装、场景质感**。
3. 分镜按镜号 + 时间码 + 景别 + 焦距 + **画面里已经发生的动作**。
4. 对白原样写在对应镜头里。
5. 排除项单独成行：不要烧录字幕、不要 BGM。

本 Demo **不会让 LLM 重写分镜**（曾经重写过，人物和画风会被文本覆盖）。只在原文后追加一段「动作与运镜（必须全部发生）」：把分镜里已有的动作再说一遍必须演出来，并加很轻的运镜。

### 文生视频（无图）

写清主体、动作、镜头、光线。没有参考图时 Demo 允许把分镜略写得更有镜头感。

### 首帧 / 首尾帧

不要写「不要把图当第一帧」。写清楚运镜：slow push-in、orbit、dolly between the two stills。本地上传与 URL 均可。

### 编辑

一句英文或中文祈使句 + `Keep everything else the same.`  
一次只改一件事。

### 续写

`Continue as …` 描述下一拍：动作延续、镜头延续、或环境变化。不要重述原片，不要换主角。

---

## 7. 和 Veo 怎么选

| | Omni 1.1 Flash | Veo 3.x |
| --- | --- | --- |
| API | Interactions，单一 `task` | `generate_videos` / predictLongRunning，mask / reference 类型更细 |
| 参考图 | 外观锁定角色/场景，张数较多 | `asset`（最多约 3）或 `style`（1） |
| 编辑 | 自然语言，无 mask | 可用 mask 做 insert/remove |
| 续写 | 自定义 3–10s，总长到 ~40s | 规则更死（帧率、固定延长等） |
| 音频开关 | 提示词 | 常有显式 generate audio |
| 适合 | 快速迭代、参考一致、自然语言改画面 | 要蒙版、要更强电影控、已有 Veo 管线 |

两者都在 Agent Platform / Vertex 上，计费与配额分开看各自文档。

---

## 8. 本 Demo 覆盖了什么、没覆盖什么

已覆盖：

- `text_to_video`、`image_to_video`（一张首帧，或两张首+尾帧）、`reference_to_video`（图，以及最多 3 段参考视频）
- `edit`（1.2 默认可多轮；1.1 重传 `gs://`。1.2 可再挂 2 张参考图）
- `extend`、`upscale`（仅 1.2；放大后不能链式编辑，后续操作重传成片）
- `seed` 与 `labels`（仅 1.2）
- GCS 出入、签名播放、血缘对比、建议气泡、图片和参考视频复用（重新生成）
- 界面按模型和能力切换；各模式隔离提示词 / 参考图 / 时长 / 分辨率，乱序切换也能测当前功能
- `/docs` 官方文档页；示例提示词与 gstatic 示例图按真实画面配对
- 失败任务把 Omni 的 `errors` 原文写进 `message`

未覆盖：

- 音频参考上传（模型卡片不支持输入；Cloud 博客写明需 allowlist）
- `stream: true` 的增量 thoughts
- 控制台 Media Studio；仅 REST
- 改配音 / 改台词

---

## 9. 权限与落地清单

1. 项目启用 Agent Platform（`aiplatform.googleapis.com`）和 Cloud Storage。
2. 调用身份：`roles/aiplatform.user`。
3. Bucket：模型读写 `gs://`；浏览器播放若用 V4 签名，运行时服务账号还需要对 **自己** 有 `roles/iam.serviceAccountTokenCreator`（Cloud Run 上用户 ADC 没有私钥）。
4. 交互永远打 `locations/global`，和 Cloud Run 部署区域无关。
5. 生成是分钟级：同步 HTTP、Cloud Run `--timeout`、反向代理都要留足时间；或像本 Demo 一样立刻返回 job id 再轮询。

---

## 10. 建议阅读顺序

1. 本文第 0 节和第 3–4 节，弄清 1.2 增量、各 task 和 `response_format` 裁剪。
2. [1.2 模型卡片](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-2-flash) 看配额与 MIME；需要对照 Preview 时再看 [1.1](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash)。
3. [参考生视频](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-references) → [编辑](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/edit-videos) → [续写](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos)。
4. 仓库里 `app/omni_client.py` 的 `build_payload()`、`app/jobs.py` 的模式分支，对照一次真实请求。
5. 打开 Demo：试用示例 → 四种 Generate → Edit → Extend。本地图走 **Upload files**。
