# Omni Flash Video Lab

[English](./README.md) · [日本語](./README.ja.md) · [中文](./README.zh-CN.md)

基于 **Gemini Omni Flash** 的本地 / Cloud Run 视频实验台。默认模型是正式版 **Omni 1.2 Flash**（`gemini-omni-1.2-flash`），页面里仍可切回 **1.1 Flash Preview**（`gemini-omni-1.1-flash-preview`），也可以手填其他模型 ID。网页里写分镜、挂参考图或参考视频，调用 Google Cloud **Agent Platform Interactions API** 完成生成、多轮编辑、续写和放大。

模型能力、参数限制、提示词写法见 [learning_guide.md](./learning_guide.md)。页面里的 **API 能力指南**（也可直接打开 `/docs`）列出每项能力、各模型的参数范围和官方页面。本文只讲 **这个仓库怎么跑、怎么部署、接口怎么用**。

### 声音偏好

创作台提供 **人声／对白、背景音乐、环境音／音效**（默认勾选）和 **字幕**（默认不勾选）。这些是提示词引导，不是 Omni 原生音频开关。**原始提示词的明确要求或禁止始终优先**；未明确时，勾选项按原场景隐含风格推断生成，未勾选项不生成。明确提供的台词、语言及配乐细节保持原意，是否开启提示词增强不影响此规则。

设置随各模式草稿、任务历史和导出保存，可通过「复用此任务设置」恢复。适用于四种生成模式，续写时只作用于新增片段；编辑不支持改对白，放大不接受文字指令。模型不保证完全遵循声音指令，应用不会在生成后强制静音或替换音轨。

`POST /api/generate` 新增 `audio_preferences: {"speech": true, "music": true, "effects": true}`，字幕通过 `no_subtitles: true` 控制。这些应用字段会转换为最终提示词，可在任务详情查看，不会作为不受支持的原生字段传给 Omni。未提供 `audio_preferences` 时兼容旧 `generate_audio` / `no_bgm` 字段。已移除隐藏的默认禁配乐和仅识别引号台词的限制。

官方依据：[音频提示指南](https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/video-gen-prompt-guide)。


## 能做什么

| 界面模式 | 实际 API `task` | 作用 |
| --- | --- | --- |
| **Generate → Text** | `text_to_video` | 纯文本分镜 → 新视频 |
| **Generate → First frame** | `image_to_video` | 一张静帧作为**第一帧** |
| **Generate → First + last** | `image_to_video` | 两张静帧之间插值运镜 |
| **Generate → References** | `reference_to_video` | 参考图，以及最多 3 段参考视频（不当首帧，也不当剪辑源） |
| **Continue edit** | `edit` | 1.2 默认用上一条 interaction 多轮再改；也可改回重传 `gs://` |
| **Edit video** | `edit` | 指定任意 `gs://` 源视频做单次局部改动。1.2 可再挂最多 2 张参考图 |
| **Extend** | `extend` | 片尾续写 3–10 秒，返回拼接后的完整成片。**1.2 源片可为任意长度**；**1.1 本 Demo 仍要求 ≤ 30 秒**。成片总长约 ≤ 40 秒 |
| **Upscale** | `upscale` | 仅 1.2。把不超过 15 秒的成片放大到更高分辨率 |

1.2 的生成和编辑是 **3–15 秒**（首尾帧仍是 3–10 秒）。1.1 仍是 **3–10 秒**。音频文件不能当输入；对白、音效和音乐写在提示词里。

## 工作原理（为何要下图、为何要 GCS）

Omni 的 Interactions API **不接受任意 HTTPS 图片链接**，只接受：

- 图片 / 视频的 **GCS `uri`**（`gs://…`）
- 或请求体内的 **inline base64 `data`**

因此本服务会：

1. 本地上传的图在浏览器里读成 base64；粘贴的公网 URL 默认把地址交给服务端下载（也可走 `/api/fetch-image`），避免站点禁止跨域时浏览器直接 `Failed to fetch`
2. 上传到 `INPUT_GCS_URI`（推荐），再把 `gs://` 交给 Omni
3. Omni 把成片写到 `OUTPUT_GCS_URI`
4. 页面用 **GCS 签名 URL** 播放，不经过本机再下一遍整段 MP4

没有配置 GCS 时会退回 base64 直传 / 本机落盘，大文件不稳定，生产环境请务必配 bucket。Cloud Run 上同样是浏览器把图发给服务，再写入 GCS，**不需要**把图先放到自己的电脑之外的网盘。

**1.2 的 Continue / Edit 可以是多轮。** 请求带 `store: true`，下一步把上一条的 interaction id 放进 `previous_interaction_id`，并且不再带 `video_config.task`，不必重传成片。存储大约保留 7 天。取消勾选「多轮编辑」，或使用 1.1 时，仍是一次新的 Interaction，把最新成片的 `gs://` 再挂进去。1.1 在 Agent Platform 上会拒绝 `previous_interaction_id`。**续写和放大始终重传成片**并带各自的 `task`，因为这个 task 和 `previous_interaction_id` 不能同时出现。放大目标按官方阶梯：360p → 720p/1080p，720p → 1080p/4k，1080p 不能到 4k。**Upscale 之后不能接着链式编辑**：放大是终点 interaction，后续 Edit / Extend 必须重传 `gs://` 成片。

## 本地运行

### 1. GCP 准备

1. 开通计费，并启用 **Agent Platform API**（`aiplatform.googleapis.com`）。
2. 建一个 GCS bucket，建议两个前缀：`omni-input/`、`omni-output/`。
3. 本机登录 Application Default Credentials：

```bash
gcloud auth application-default login
gcloud config set project YOUR_PROJECT_ID
```

运行身份需要能调 Agent Platform，以及对该 bucket 的读写（本机 ADC 通常是你的用户账号）。

### 2. 配置环境变量

```bash
cp .env.example .env
```

至少改这三项：

```bash
GOOGLE_CLOUD_PROJECT=your-gcp-project-id
INPUT_GCS_URI=gs://your-bucket/omni-input/
OUTPUT_GCS_URI=gs://your-bucket/omni-output/
```

若本机访问 `oauth2.googleapis.com` / `aiplatform.googleapis.com` 需要代理（例如 Clash Mixed Port），保留 `.env.example` 里的 `HTTP_PROXY` / `HTTPS_PROXY` / `GOOGLE_API_TRUST_ENV=true`。Cloud Run 上这些会被忽略。

其余变量说明见下文「环境变量」。

### 3. 安装并启动

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

打开 [http://127.0.0.1:8000](http://127.0.0.1:8000)。右上角健康检查应显示已配置项目与 GCS。

生成通常要 **1–3 分钟或更久**；任务在服务端异步跑，页面轮询 `/api/jobs/{id}`。

## 界面怎么用

1. 先选 Generate 下的能力：**Text → video** / **First frame** / **First + last** / **References**。点「试用示例」或 **Load example** 填入对应提示词（和示例图）。
2. 需要图片时，可 **Upload files** 从本地上传，或粘贴公网 URL。References 模式用 `@图片1` 绑定角色。
3. 确认时长、分辨率、画幅，以及音频相关开关。
4. 点主按钮生成。成片出现在右侧 Result。
5. 用「以此结果继续编辑 / 编辑 / 续写 / 放大」，或点建议气泡，在同一条素材上迭代。顺序可以乱跳：Generate → Upscale → Edit → Extend 都可以。
6. **重新生成**会复用该任务已经上传到 GCS 的图片（文生视频则复用分镜）。

每个模式各自记住提示词、时长、分辨率和参考图列表，避免上一个功能的内容串进下一个。Edit / Continue / Extend 只有指令里写了 `@图片1` 才会上传可选参考图；纯改光、改运镜不带参考图。

### 生成能力怎么选

- **Text → video**：不需要图。适合没有角色一致性的镜头。
- **First frame**：图是成片第 0 帧。提示词只写怎么动，不要重写人物长相。
- **First + last**：两张图分别是开场和收束，模型在中间插值运镜。API 仍是 `image_to_video`。
- **References**：图锁定人物/画风，**不当**第一帧。分镜用 `@图片N` 绑定。
- **Continue / Edit**：`task=edit`。1.2 源片最长 **15 秒**，1.1 最长 **10 秒**。指令宜短。1.2 可附最多 2 张参考图。
- **Extend**：本次续写 **3–10s**（不是 15s）。**1.2** 源片可为任意长度，但有两层含义要分开：**看约 10 秒**（上下文）和 **留约 30 秒**（成片里保留的源片末尾）；更早画面会裁掉，再接上新段，成片约 ≤ **40 秒**。**1.1** 在本 Demo 里仍拦截超过 30 秒的源片。不要按「15+15+15」规划到 45 秒。
- **Upscale**：仅 1.2，`task=upscale`，源片不超过 15 秒。请求不带文字，只把分辨率提高到比源片更高的一档。官方建议先用 360p / 720p 出草稿，再放大到 1080p 或 4K。这不是 Cloud Transcoder 的转码。
- **Seed / Labels**：仅 1.2。Seed 写入 `generation_config.seed`；labels 用小写 `key=value`，用来给账单分组。

## 创作台、作品集与任务历史

- **创作台**：八项功能（文生、首帧、首尾帧、参考、编辑、多轮编辑、续写、放大）。每项只显示自己的参数，各自保存草稿，乱序切换也能直接测当前功能。刷新后草稿和正在看的作品都会回来。
- **作品预览**：每条成片都能一键作为源片进入 多轮编辑 / 编辑 / 续写 / 放大，也能用「复用此任务设置」把当时的输入放回创作台。标签页里有输入素材、实际提交的提示词与请求、任务时间线，以及同一条血缘链上的衍生版本（可并排对比播放）。
- **作品集**：成片可命名、分组、备注、收藏、归档，回收站可恢复；支持搜索和筛选。
- **任务历史**：全部任务，可按状态筛选。打开任务是在对话框里预览，不动创作台的草稿。
- **工作区**：历史归属于当前浏览器工作区（http-only cookie）。在「工作区 → 导出恢复文件」后，可以在另一台设备打开同一个工作区。任务记录和作品集信息存在 `OUTPUT_GCS_URI/workspaces/`（可用 `WORKSPACE_STORE_URI` 改位置）；没有配置 bucket 时写到本地 `outputs/workspaces/`。服务重启后，已经拿到 interaction id 的任务会继续轮询同一个 id，不会重复提交。
- 界面语言：English / 日本語 / 中文（在工作区对话框里切换）。提示词按原文发送。

## 环境变量

| 变量 | 含义 |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | GCP 项目 |
| `INPUT_GCS_URI` | 参考图上传前缀 |
| `OUTPUT_GCS_URI` | 成片输出前缀；同时打开 `delivery=uri` |
| `OMNI_MODEL` | 默认 `gemini-omni-1.2-flash`。页面每次请求还可以改模型 |
| `VIDEO_PLAYBACK` | `gcs_signed`（默认）或 `local` |
| `GCS_SIGNING_SERVICE_ACCOUNT` | Cloud Run 上签 URL 用的运行时服务账号 |
| `PROMPT_ENHANCE` | 是否写动作补丁 / 续写润色 / 建议气泡 |
| `PROMPT_ENHANCE_MODEL` | 默认 `gemini-2.5-flash` |
| `PROMPT_ENHANCE_LOCATION` | 默认 `global` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 仅本地；Cloud Run 会剥离 |
| `GOOGLE_API_TRUST_ENV` | 本地走系统代理时设 `true` |
| `OUTPUT_DIR` | 本地落盘目录；Cloud Run 默认 `/tmp` |
| `WORKSPACE_STORE_URI` | 任务记录与作品集信息的位置。默认 `OUTPUT_GCS_URI/workspaces` |
| `MAX_ACTIVE_JOBS` | 这个实例上同时进行的生成数上限，默认 8 |

Omni 的 Interactions URL 固定为 `locations/global`，与部署区域无关。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/docs` | API 能力指南（同一个页面的指南视图） |
| `GET` / `POST` | `/api/workspace` | 打开当前浏览器工作区 / 用恢复密钥切换工作区 |
| `GET` | `/api/workspace/export` | 下载工作区恢复文件 |
| `POST` | `/api/assets` | 上传一张参考图或一段视频（≤ 20 MB），返回 `gs://` |
| `GET` | `/api/health` | 项目、默认模型、1.1/1.2 能力表、GCS、代理、runtime |
| `GET` | `/api/example` | 参考图模式的默认示例 |
| `GET` | `/api/examples` | 各 generate task / edit / extend 的示例；提示词与示例图按真实画面配对 |
| `POST` | `/api/generate` | 启动任务；`generate_task` 指定文生 / 首帧 / 首尾帧 / 参考 |
| `GET` | `/api/submissions/{request_id}` | 按提交时的 `request_id` 查这次提交对应的任务（页面中断后用来确认是否已启动） |
| `GET` | `/api/jobs` | 当前工作区的全部任务（列表用的精简字段） |
| `PATCH` | `/api/jobs/{id}` | 改标题、分组、备注、收藏、归档、回收站（带 `version` 防止覆盖） |
| `POST` | `/api/jobs/{id}/recover` | 按已保存的 interaction id 重新查询状态，不重新生成 |
| `GET` | `/api/jobs/{id}/export` | 下载完整任务记录 |
| `GET` | `/api/jobs/{id}/download` | 下载成片 |
| `GET` | `/api/jobs/{id}/assets/{images\|videos}/{n}` | 这条任务用过的参考素材 |
| `GET` | `/api/jobs/{id}` | 轮询状态；含 `editable`、`reuse_ready`、`duration_sec` |
| `GET` | `/api/jobs/{id}/video` | 本机文件或 302 到签名 URL |
| `GET` | `/api/jobs/{id}/lineage` | 同一条生成链上的成片 |
| `GET` | `/api/jobs/{id}/suggestions` | 2–3 条可点的下一步（按分镜生成，缓存一次） |
| `GET` | `/api/video?uri=gs://…` | 给粘贴的源视频签播放 URL（仅本 Demo 的 bucket） |
| `POST` | `/api/test-gcs-upload` | 只测浏览器 → GCS，不调 Omni |
| `GET` | `/api/fetch-image?url=` | 服务端代下参考图 |

`POST /api/generate` 常用字段：`prompt`、`model`、`generate_task`、`reference_images`、`reference_videos`（base64 或 `gs://`）、`image_urls`、`duration`（1.2 为 3–15，首尾帧和续写另有上限）、`resolution`、`aspect_ratio`、`mode`（含 `upscale`）、`seed`、`labels`、`stateful`、`source_job_id` / `source_video_uri`、`reuse_job_id`、`enhance_prompt`、`request_id`（同一个 `request_id` 重复提交只会得到同一条任务）。除 `/api/health`、`/api/workspace`、`/api/examples` 外，接口都需要先 `GET /api/workspace` 拿到工作区 cookie。

超过该模型编辑上限的成片，接口仍可 **extend**；1.2 在 15 秒以内还可以 **upscale**。

## 部署到 Cloud Run

Cloud Run 上 token 来自实例 metadata，GCS 走 Google 内网，**不需要本机代理**。

```bash
PROJECT_ID=your-gcp-project BUCKET=your-gcs-bucket ./deploy/cloud-run.sh
```

可选：`REGION=asia-east1`、`SERVICE=…`、`OMNI_MODEL=…`、`PROMPT_ENHANCE_MODEL=…`、`ALLOW_UNAUTHENTICATED=false`。

脚本会开通 API、创建运行时服务账号（Vertex 用户 + bucket 对象管理员 + 自我 impersonate 以便签 URL），并从源码部署。本机 **不需要 Docker**，镜像由 Cloud Build 按 `Dockerfile` 构建。

检测到 `K_SERVICE` 后：

- 剥离所有代理环境变量，并设 `GOOGLE_API_TRUST_ENV=false`
- 临时文件写 `/tmp`
- `/api/health` 的 `runtime` 为 `cloud_run`

这些部署参数是有意的，不是默认值随便填：

- 生成在 HTTP 返回之后仍会跑很久，并且由发起它的实例轮询 → `--no-cpu-throttling`、`--max-instances 1`
- 空闲缩到 0（`--min-instances 0`）不产生待机费用；任务记录和作品集信息在 GCS 里，冷启动后照常打开
- 实例在生成中途重启时，已拿到 interaction id 的任务会在工作区再次打开时继续轮询
- 默认 `--allow-unauthenticated`。大陆网络经常打不开 `*.run.app`，需要的话自己挂自定义域名 + 负载均衡

多实例之前需要先把「哪个实例在轮询哪条任务」也放到外部协调（例如 Firestore / Redis 锁）。

## 仓库结构

```
app/config.py            环境变量、Cloud Run 检测、代理
app/omni_client.py       Interactions 请求、轮询、解析视频
app/model_catalog.py     1.1 / 1.2 的时长、编辑、放大和参考上限
app/jobs.py              异步任务、按模型校验时长、参考图/视频复用、任务记录持久化
app/workspace.py         工作区身份与记录存储（GCS 对象 + generation 校验，或本地 JSON）
app/prompt_utils.py      解析 URL、@图片N / @视频N
app/prompt_enhancer.py   动作补丁、续写润色、建议气泡
app/gcs_util.py          上传、签名 URL、时长探测
app/main.py              FastAPI
static/                  单页界面：创作台、作品集、任务历史、API 能力指南（app.js / i18n.js / style.css）
tests/                   请求体、示例配对、失败信息等回归
deploy/cloud-run.sh      一键部署
learning_guide.md        Omni 能力与提示词
.env.example             配置模板（不要提交真实 .env）
```

## 已知限制（与本 Demo 相关）

- 生成慢，请当异步任务用，不要把 HTTP 超时设太短。
- 对白靠提示词约束；Omni **没有** Veo 那种独立的 `generate_audio` 字段。声音勾选通过提示词引导，原始提示词的明确要求优先。
- 当前 API **不支持改配音 / 改对白内容**（画面编辑可以）。
- 续写按官方：**源片任意长度**；`response_format.duration` 为本次追加 3–10 秒。文档里的 `aspect_ratio` 线上会拒，本 Demo 不传。**不会**为规避错误而裁短源片。
- 编辑过长视频会返回 `Editing duration N exceeds maximum duration …`。1.1 的上限是 10 秒，1.2 的模型卡片是 15 秒。
- 音频参考没有入口。模型卡片写 Audio 输入不支持；Cloud 博客写明需 allowlist。
- 放大请求使用 `task=upscale`，input 只有源视频。带文字会返回 `Text input is not supported for upscale task`。
- 放大之后的 Edit / Extend 不能再用 `previous_interaction_id` 接着改，必须重传成片的 `gs://`。
- Omni 返回 `status=failed` 时，任务的 `message` 会带上接口 `errors` 原文（例如 `prohibited_content`），不再只写一句 `Omni interaction failed`。
- 素材站常见的 **fragmented / CMAF MP4**（`iso5` + `moof`，`mvhd` 时长为 0）会 remux 成 progressive（片长不变），meta 可见 `source_remuxed: true`。

更完整的模型能力表、提示词模式、以及本 Demo 未覆盖的 API，见 [learning_guide.md](./learning_guide.md)。
