# Gemini Omni 1.1 Flash 学习指南

本文说明 **Gemini Omni 1.1 Flash** 能做什么、请求怎么组、参数上限是什么，以及和 **Veo**、和本仓库 Demo 的差别。规格以 Google Cloud 文档为准，并标注了本 Demo 对 **线上 API** 的实测修正。

官方入口：

- 模型卡片：[Gemini Omni 1.1 Flash Preview](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash)
- 视频总览：[Generate videos with Agent Platform](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/overview)
- Interactions API：[locations/global/interactions](https://docs.cloud.google.com/gemini-enterprise-agent-platform/reference/models/interactions-api)
- 产品介绍：[Build with Gemini Omni 1.1 Flash](https://blog.google/innovation-and-ai/technology/developers-tools/build-with-gemini-omni-1-1-flash/)

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
- 视频输入：每条 prompt 最多 **3** 段；卡片上的「单段最长 10s」主要约束 **编辑类** 输入，续写另有 30s 上限（见第 4 节）。
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

文档：[Generate videos from text](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-text-prompts)（路径以控制台「What's next」为准）。

### 3.2 `image_to_video` — 图作**第一帧**（可选最后一帧）

把图片当作 **字面意义上的起始画面**，而不是「长得像这张图的角色」。适合运镜、转场、loop。

- 一张图：从该帧开始演。
- 两张图（首 + 尾）：在两帧之间插值，适合环绕、推拉、无缝循环。见 [first and last frames](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-first-and-last-frames)。

**本 Demo 未实现。** 界面里的参考图走的是下一节的 `reference_to_video`。

### 3.3 `reference_to_video` — 参考图 / 参考视频「像它」，不当首帧

参考图提供人物、服装、场景、画风；模型应 **仿其外观去演分镜**，而不是把图贴成第一帧。官方提示里常带：

> Use the given image(s) as references for video generation. The images should not be used as literal initial frames.

也可以同时挂参考 **视频**（风格或主体），本 Demo 只用图片。

绑定方式（本 Demo 按官方 cookbook 做的）：

1. 提示词顶部：`[# References <IMAGE_REF_0>@Image1 <IMAGE_REF_1>@Image2 …]`
2. 分镜里用 `<IMAGE_REF_0>` 指第一张图。用户写 `@图片1` / `Image 1` 时，服务端会改成 0-based 标签。
3. 不要在文本里再散文式重写五官和画风，否则文本会 **盖过** 参考图。

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

- 源片 **≤ 10 秒**。16s 会报 `Editing duration 16 exceeds maximum duration 10.`
- `response_format` **只接受 `resolution`**（外加可选的 `delivery` / `gcs_uri`）。传 `aspect_ratio` 或 `duration` 会被拒。
- 当前 **不能改声音 / 台词**。

文档：[Edit videos](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/edit-videos)。

多轮编辑在文档里有时写成把上一次 `interaction.id` 塞回去。**1.1 在 Agent Platform 上会拒 `previous_interaction_id`。** 正确做法是每次新请求，把最新成片的 `gs://` 再当 `input.video`。本 Demo 的 Continue 就是这样。

### 3.5 `extend` — 续写（往后接，返回整段）

输入：源视频 + 「接下来几秒发生什么」。模型从片尾接着演，并尽量保持人物、场景、画风。

官方上限（[Extend videos](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos)）：

| | Omni Flash | Veo（对照） |
| --- | --- | --- |
| 源片长度 | **1–30 秒** | 1–30 秒，且常要求 24fps MP4 |
| 单次 `duration` | **3–10 秒**（本次新增长度） | Veo 文档里有固定约 7s 等差异 |
| 成片总长 | **最长约 40 秒** | 约 37 秒 |

返回的是 **拼接后的完整视频**：8s 源片再续 8s → 得到约 **16s** 文件，不是只返回新的 8s。因此：

- 再 Edit 会失败（超过 10s）
- 还可以继续 Extend，直到接近 40s

`response_format` 实测 **只传 `duration`**。文档示例里的 `aspect_ratio` 会被线上拒绝。分辨率沿用源片，不要在 extend 请求里再塞 `resolution`。

提示词应写 **片尾之后** 的动作/运镜/环境变化，不要把原片再描述一遍，也不要新开无关场景或第二个主角。

---

## 4. `response_format` 对照表（务必按 task 裁剪）

API 对「这个 task 不该出现的字段」是直接 4xx，而不是忽略。

| `task` | `aspect_ratio` | `resolution` | `duration` | `delivery` / `gcs_uri` |
| --- | --- | --- | --- | --- |
| `text_to_video` / `image_to_video` / `reference_to_video` | ✓ | ✓ | ✓（3s–10s） | 推荐 |
| `edit` | ✕ | ✓ | ✕（跟源片） | 推荐 |
| `extend` | ✕（文档有、线上拒） | ✕ | ✓（本次续写 3s–10s） | 推荐 |

画幅取值：`16:9`、`9:16`。分辨率：`360p`、`720p`、`1080p`、`4k`。时长字符串必须带 `s`，例如 `"8s"`。

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

- `text_to_video`（无参考图）与 `reference_to_video`（浏览器图 → GCS）
- `edit`（Continue / Edit 两个入口，同一 task）
- `extend`（完整拼接成片、10s 编辑上限提示）
- GCS 出入、签名播放、血缘对比、建议气泡、参考图复用（重新生成）

未覆盖（官方支持，可自行扩展）：

- `image_to_video` 首帧 / 首尾帧插值
- 参考 **视频**（`reference_to_video` 的 video input）
- 官方异步 `background: true`（本 Demo 自己 poll）
- 控制台 Media Studio；仅 REST
- 把成片当下一轮的参考视频（只把成片当 edit/extend 的 source）

---

## 9. 权限与落地清单

1. 项目启用 Agent Platform（`aiplatform.googleapis.com`）和 Cloud Storage。
2. 调用身份：`roles/aiplatform.user`。
3. Bucket：模型读写 `gs://`；浏览器播放若用 V4 签名，运行时服务账号还需要对 **自己** 有 `roles/iam.serviceAccountTokenCreator`（Cloud Run 上用户 ADC 没有私钥）。
4. 交互永远打 `locations/global`，和 Cloud Run 部署区域无关。
5. 生成是分钟级：同步 HTTP、Cloud Run `--timeout`、反向代理都要留足时间；或像本 Demo 一样立刻返回 job id 再轮询。

---

## 10. 建议阅读顺序

1. 本文第 3–4 节，弄清五个 task 和 `response_format` 裁剪。
2. [模型卡片](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/gemini/omni-1-1-flash) 看配额与 MIME。
3. [参考生视频](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/generate-videos-from-references) → [编辑](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/edit-videos) → [续写](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/extend-videos)。
4. 仓库里 `app/omni_client.py` 的 `build_payload()`、`app/jobs.py` 的模式分支，对照一次真实请求。
5. 跑通 [README](./README.md) 的 Load example → Generate → Edit → Extend。
