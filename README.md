# Omni Flash Video Lab

基于 **Gemini Omni 1.1 Flash**（`gemini-omni-1.1-flash-preview`）的本地 / Cloud Run 视频实验台：网页里写分镜、挂参考图，调用 Google Cloud **Agent Platform Interactions API** 完成生成、编辑、续写。

模型能力、参数限制、提示词写法见 [learning_guide.md](./learning_guide.md)。本文只讲 **这个仓库怎么跑、怎么部署、接口怎么用**。

## 能做什么

| 界面模式 | 实际 API `task` | 作用 |
| --- | --- | --- |
| **Generate → Text** | `text_to_video` | 纯文本分镜 → 新视频 |
| **Generate → First frame** | `image_to_video` | 一张静帧作为**第一帧** |
| **Generate → First + last** | `image_to_video` | 两张静帧之间插值运镜 |
| **Generate → References** | `reference_to_video` | 参考图锁定人物/画风（不当首帧） |
| **Continue edit** | `edit` | 以上一条成片为源，自然语言再改一刀 |
| **Edit video** | `edit` | 指定任意 `gs://` 源视频做单次局部改动 |
| **Extend** | `extend` | 在片尾续写 3–10 秒，返回**拼接后的完整成片** |

本 Demo **没有**接参考视频（`reference_to_video` 的 video input）。首尾帧与文生、参考生视频均已在界面里提供。

## 工作原理（为何要下图、为何要 GCS）

Omni 的 Interactions API **不接受任意 HTTPS 图片链接**，只接受：

- 图片 / 视频的 **GCS `uri`**（`gs://…`）
- 或请求体内的 **inline base64 `data`**

因此本服务会：

1. 浏览器读取图片（**本地上传**，或粘贴公网 URL；URL 需要目标站点允许 CORS）
2. 上传到 `INPUT_GCS_URI`（推荐），再把 `gs://` 交给 Omni
3. Omni 把成片写到 `OUTPUT_GCS_URI`
4. 页面用 **GCS 签名 URL** 播放，不经过本机再下一遍整段 MP4

没有配置 GCS 时会退回 base64 直传 / 本机落盘，大文件不稳定，生产环境请务必配 bucket。Cloud Run 上同样是浏览器把图发给服务，再写入 GCS，**不需要**把图先放到自己的电脑之外的网盘。

**Continue 不是多轮 chat。** 该模型在 Agent Platform 上会拒绝 `previous_interaction_id`，所以每一轮 Continue / Edit / Extend 都是一次新的 Interaction，把上一条成片的 `gs://` 再挂进去。

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
5. 用「以此结果继续编辑 / 编辑 / 续写」，或点建议气泡，在同一条素材上迭代。
6. **重新生成**会复用该任务已经上传到 GCS 的图片（文生视频则复用分镜）。

### 生成能力怎么选

- **Text → video**：不需要图。适合没有角色一致性的镜头。
- **First frame**：图是成片第 0 帧。提示词只写怎么动，不要重写人物长相。
- **First + last**：两张图分别是开场和收束，模型在中间插值运镜。API 仍是 `image_to_video`。
- **References**：图锁定人物/画风，**不当**第一帧。分镜用 `@图片N` 绑定。
- **Continue / Edit**：`task=edit`。源片 **不得超过 10 秒**。指令宜短。
- **Extend**：本次续写 3–10s，返回拼接后的完整成片。源片 1–30s，总长约 40s。

Comparison 面板可以把同一条血缘链上的成片并排同步播放。任务状态存在进程内存里，重启服务或 Cloud Run 缩到 0 后历史会丢，**成片仍在 GCS**。

## 环境变量

| 变量 | 含义 |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT` | GCP 项目 |
| `INPUT_GCS_URI` | 参考图上传前缀 |
| `OUTPUT_GCS_URI` | 成片输出前缀；同时打开 `delivery=uri` |
| `OMNI_MODEL` | 默认 `gemini-omni-1.1-flash-preview` |
| `VIDEO_PLAYBACK` | `gcs_signed`（默认）或 `local` |
| `GCS_SIGNING_SERVICE_ACCOUNT` | Cloud Run 上签 URL 用的运行时服务账号 |
| `PROMPT_ENHANCE` | 是否写动作补丁 / 续写润色 / 建议气泡 |
| `PROMPT_ENHANCE_MODEL` | 默认 `gemini-2.5-flash` |
| `PROMPT_ENHANCE_LOCATION` | 默认 `global` |
| `HTTP_PROXY` / `HTTPS_PROXY` | 仅本地；Cloud Run 会剥离 |
| `GOOGLE_API_TRUST_ENV` | 本地走系统代理时设 `true` |
| `OUTPUT_DIR` | 本地落盘目录；Cloud Run 默认 `/tmp` |

Omni 的 Interactions URL 固定为 `locations/global`，与部署区域无关。

## HTTP API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/api/health` | 项目、模型、GCS、代理、runtime |
| `GET` | `/api/example` | 参考图模式的默认示例 |
| `GET` | `/api/examples` | 各 generate task / edit / extend 的示例提示词 |
| `POST` | `/api/generate` | 启动任务；`generate_task` 指定文生 / 首帧 / 首尾帧 / 参考 |
| `GET` | `/api/jobs` | 最近任务 |
| `GET` | `/api/jobs/{id}` | 轮询状态；含 `editable`、`reuse_ready`、`duration_sec` |
| `GET` | `/api/jobs/{id}/video` | 本机文件或 302 到签名 URL |
| `GET` | `/api/jobs/{id}/lineage` | 同一条生成链上的成片 |
| `GET` | `/api/jobs/{id}/suggestions` | 2–3 条可点的下一步（按分镜生成，缓存一次） |
| `GET` | `/api/video?uri=gs://…` | 给粘贴的源视频签播放 URL（仅本 Demo 的 bucket） |
| `POST` | `/api/test-gcs-upload` | 只测浏览器 → GCS，不调 Omni |
| `GET` | `/api/fetch-image?url=` | 服务端代下参考图 |

`POST /api/generate` 常用字段：`prompt`、`generate_task`、`reference_images`（本地/URL 读出的 base64）、`image_urls`、`duration`（3–10）、`resolution`、`aspect_ratio`、`mode`、`source_job_id` / `source_video_uri`、`reuse_job_id`、`enhance_prompt`。

超过 10 秒的成片，建议接口只给 **extend**，不再给 edit。

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

- `JobStore` 在 **进程内存** 里，生成在 HTTP 返回之后仍会跑很久 → `--no-cpu-throttling`、`--max-instances 1`
- 空闲缩到 0（`--min-instances 0`）不产生待机费用；冷启动后内存历史清空，成片仍在 `OUTPUT_GCS_URI`
- 生成期间请保持页面打开，轮询能把实例撑住
- 默认 `--allow-unauthenticated`。大陆网络经常打不开 `*.run.app`，需要的话自己挂自定义域名 + 负载均衡

多实例之前必须先把任务状态迁到 Redis / Firestore 之类的外部存储。

## 仓库结构

```
app/config.py            环境变量、Cloud Run 检测、代理
app/omni_client.py       Interactions 请求、轮询、解析视频
app/jobs.py              异步任务、10s 编辑上限、参考图复用
app/prompt_utils.py      解析 URL、@图片N → <IMAGE_REF_N>、台词锁
app/prompt_enhancer.py   动作补丁、续写润色、建议气泡
app/gcs_util.py          上传、签名 URL、时长探测
app/main.py              FastAPI
static/                  前端（能力切换、本地上传、示例芯片）
deploy/cloud-run.sh      一键部署
learning_guide.md        Omni 能力与提示词
.env.example             配置模板（不要提交真实 .env）
```

## 已知限制（与本 Demo 相关）

- 生成慢，请当异步任务用，不要把 HTTP 超时设太短。
- 对白靠提示词约束；Omni **没有** Veo 那种独立的 `generate_audio` 字段。打开「Generate dialogue」类措辞容易多出旁白，本 Demo 会按分镜里的引号锁台词。
- 当前 API **不支持改配音 / 改对白内容**（画面编辑可以）。
- 文档里 extend 的 `response_format` 写了 `aspect_ratio`，**线上会拒**；本仓库只传 `duration`。
- 编辑过长视频会返回 `Editing duration N exceeds maximum duration 10`。

更完整的模型能力表、提示词模式、以及本 Demo 未覆盖的 API，见 [learning_guide.md](./learning_guide.md)。
