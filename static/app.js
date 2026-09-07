const promptEl = document.getElementById("prompt");
const imageListEl = document.getElementById("imageList");
const generateBtn = document.getElementById("generateBtn");
const testGcsBtn = document.getElementById("testGcsBtn");
const statusLine = document.getElementById("statusLine");
const jobIdEl = document.getElementById("jobId");
const jobLog = document.getElementById("jobLog");
const thoughtsBlock = document.getElementById("thoughtsBlock");
const thoughtsEl = document.getElementById("thoughts");
const player = document.getElementById("player");
const stageEmpty = document.getElementById("stageEmpty");
const jobListEl = document.getElementById("jobList");
const healthEl = document.getElementById("health");
const guideTitle = document.getElementById("guideTitle");
const guideSteps = document.getElementById("guideSteps");
const guideNote = document.getElementById("guideNote");
const sourceBox = document.getElementById("sourceBox");
const refsSection = document.getElementById("refsSection");
const sourceVideoField = document.getElementById("sourceVideoField");
const sourceJobIdEl = document.getElementById("sourceJobId");
const sourceVideoUriEl = document.getElementById("sourceVideoUri");
const durationField = document.getElementById("durationField");
const resolutionField = document.getElementById("resolutionField");
const aspectField = document.getElementById("aspectField");
const promptLabel = document.getElementById("promptLabel");
const resultActions = document.getElementById("resultActions");
const audioToggles = document.getElementById("audioToggles");
const enhanceToggle = document.getElementById("enhanceToggle");
const enhancePromptEl = document.getElementById("enhancePrompt");
const promptBlock = document.getElementById("promptBlock");
const promptSent = document.getElementById("promptSent");
const promptDiffNote = document.getElementById("promptDiffNote");
const suggestions = document.getElementById("suggestions");
const suggestionBubbles = document.getElementById("suggestionBubbles");
const suggestionsTitle = document.getElementById("suggestionsTitle");
const comparison = document.getElementById("comparison");
const comparisonGrid = document.getElementById("comparisonGrid");
const comparisonHint = document.getElementById("comparisonHint");

let pollTimer = null;
let imageCount = 0;
let parseTimer = null;
let lastFilledUrlsKey = "";
let currentMode = "generate";
let selectedJob = null;

const MODE_GUIDE = {
  generate: {
    title: "步骤 1 · Generate — 由提示词与参考图生成视频",
    steps: [
      "粘贴分镜提示词；若文本中包含参考图 URL 数组，会自动填入下方列表。",
      "参考图由浏览器读取，经服务端暂存后上传至 GCS，再提交 Omni。",
      "成片写入 OUTPUT_GCS_URI，右侧通过签名 URL 播放。",
      "时长、分辨率、画幅可在下方参数区调整。",
    ],
    note: "生成完成后，可在右侧选择该结果，继续进行 Continue / Edit / Extend。",
    placeholder:
      "粘贴分镜提示词与参考图 URL 数组，系统会自动解析并填充下方参考图列表。",
    button: "Generate video",
    promptLabel: "Prompt（分镜 / 参考图）",
  },
  continue: {
    title: "步骤 2 · Continue edit — 基于上一条结果继续修改",
    steps: [
      "在右侧选择一条已完成的结果，或点击「以此结果继续编辑」自动填入源视频。",
      "以简短指令描述改动，例如调整光线、移除画面中的某个元素。",
      "若未填写，服务端会自动补充 Keep everything else the same.",
      "每轮结果都会成为下一轮可选的源视频，可反复迭代。",
    ],
    note: "建议指令简短明确；描述过多细节可能影响未提及的画面内容。画幅与时长沿用源视频；源视频需在 10s 以内。",
    placeholder: "例：Make the spaceship glow blue. / 将背景调整为傍晚光线。",
    button: "Continue edit",
    promptLabel: "Edit instruction（简短指令）",
  },
  edit: {
    title: "步骤 3 · Edit video — 对指定视频执行单次编辑（task=edit）",
    steps: [
      "填写源视频：已完成任务的 gs:// video_uri，或手动粘贴任意 GCS 路径。",
      "填写简短的改动说明，并保留 Keep everything else the same.",
      "适用场景：移除画面元素、调整天气与光影等局部修改。",
      "与 Continue 的区别仅在于源视频的填写方式，调用的是同一接口。",
    ],
    note: "源视频不得超过 10s，因此续写过的片子无法再 Edit。当前 API 不支持语音编辑。画幅与时长沿用源视频。",
    placeholder: "例：Remove the tablet from her hands. Keep everything else the same.",
    button: "Edit video",
    promptLabel: "Edit instruction",
  },
  extend: {
    title: "步骤 4 · Extend — 在原片基础上续写（task=extend）",
    steps: [
      "选择源视频 gs:// URI（可从右侧已完成任务填入）。",
      "描述后续画面内容，例如角色动作变化或镜头推进。",
      "单次续写时长由 Duration 控制（3–10s），可多次累加。",
      "返回的是拼接后的完整视频，例如 8s 源片续写 8s 会得到 16s 成片。",
    ],
    note: "源视频可到 30s，成片最长 40s。注意续写后长度超过 10s 就不能再 Edit 了。分辨率沿用源视频。",
    placeholder: "例：Continue as they watch the interface light up and smile at each other.",
    button: "Extend video",
    promptLabel: "What happens next",
  },
};

function setStatus(text) {
  statusLine.textContent = text;
}

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll(".mode-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });
  const g = MODE_GUIDE[mode];
  guideTitle.textContent = g.title;
  guideSteps.innerHTML = "";
  for (const step of g.steps) {
    const li = document.createElement("li");
    li.textContent = step;
    guideSteps.appendChild(li);
  }
  guideNote.textContent = g.note;
  promptLabel.textContent = g.promptLabel;
  promptEl.placeholder = g.placeholder;
  generateBtn.textContent = g.button;

  const isGenerate = mode === "generate";
  sourceBox.hidden = isGenerate;
  sourceVideoField.hidden = isGenerate;
  refsSection.hidden = !isGenerate;
  testGcsBtn.hidden = !isGenerate;
  audioToggles.hidden = !isGenerate;

  // The API rejects response_format fields a task does not accept, so only
  // offer the controls that actually reach the request.
  durationField.hidden = mode === "continue" || mode === "edit";
  resolutionField.hidden = mode === "extend";
  aspectField.hidden = !isGenerate;

  // Edits must stay terse, so polishing is offered only where it helps.
  enhanceToggle.hidden = !(isGenerate || mode === "extend");
}

function applySourceJob(job) {
  if (!job) return;
  selectedJob = job;
  sourceJobIdEl.value = job.id || "";
  sourceVideoUriEl.value = job.video_uri || "";
  if (job.status === "completed" && job.video_uri) {
    resultActions.hidden = false;
  }
}

function extractUrlsLocally(text) {
  const urls = [];
  let cleaned = text;

  const tryParseArray = (raw) => {
    try {
      return JSON.parse(raw);
    } catch {
      try {
        return JSON.parse(raw.replace(/,\s*]/, "]"));
      } catch {
        return null;
      }
    }
  };

  const labeled =
    /(?:参考图|references?|image[_ ]?urls?)\s*[:：]?\s*(\[[\s\S]*?\])/gi;
  let match;
  while ((match = labeled.exec(text)) !== null) {
    const parsed = tryParseArray(match[1]);
    if (Array.isArray(parsed)) {
      for (const item of parsed) {
        if (typeof item === "string" && /^https?:\/\//i.test(item)) urls.push(item);
      }
      cleaned = cleaned.replace(match[0], "\n");
    }
  }

  if (!urls.length) {
    const bare = text.match(/\[\s*"https?:\/\/[\s\S]*?\]/);
    if (bare) {
      const parsed = tryParseArray(bare[0]);
      if (Array.isArray(parsed)) {
        for (const item of parsed) {
          if (typeof item === "string" && /^https?:\/\//i.test(item)) urls.push(item);
        }
        cleaned = cleaned.replace(bare[0], "\n");
      }
    }
  }

  if (!urls.length) {
    const stripped = text.trim();
    if (stripped.startsWith("[") && stripped.endsWith("]")) {
      const parsed = tryParseArray(stripped);
      if (
        Array.isArray(parsed) &&
        parsed.every((x) => typeof x === "string" && /^https?:\/\//i.test(x))
      ) {
        return { prompt: "", urls: uniqueUrls(parsed) };
      }
    }
  }

  if (!urls.length) {
    const loose = text.match(/https?:\/\/[^\s"'<>\]]+/gi) || [];
    for (const u of loose) urls.push(u);
    for (const u of urls) cleaned = cleaned.replace(u, "");
  }

  cleaned = cleaned
    .replace(/^\s*(参考图|references?|image[_ ]?urls?)\s*[:：]?\s*$/gim, "")
    .replace(/\n{3,}/g, "\n\n")
    .trim();

  return { prompt: cleaned, urls: uniqueUrls(urls) };
}

function uniqueUrls(list) {
  const seen = new Set();
  const out = [];
  for (let u of list) {
    u = String(u).trim().replace(/[.,;]+$/, "");
    if (!u || seen.has(u)) continue;
    seen.add(u);
    out.push(u);
  }
  return out;
}

function setImageRows(urls) {
  imageListEl.innerHTML = "";
  imageCount = 0;
  if (!urls.length) {
    addImageRow();
    return;
  }
  for (const url of urls) addImageRow(url);
}

function addImageRow(url = "") {
  imageCount += 1;
  const idx = imageCount;
  const row = document.createElement("div");
  row.className = "image-row";
  row.dataset.idx = String(idx);

  const thumb = document.createElement("div");
  thumb.className = "thumb placeholder";
  thumb.textContent = `图 ${idx}`;

  const mid = document.createElement("div");
  const label = document.createElement("div");
  label.className = "idx";
  label.textContent = `@图片${idx}`;
  const input = document.createElement("input");
  input.type = "url";
  input.placeholder = "https://… image URL";
  input.value = url;
  const refreshThumb = () => {
    const current = row.querySelector(".thumb");
    if (current) updateThumb(current, input.value.trim(), idx);
  };
  input.addEventListener("change", refreshThumb);
  input.addEventListener("blur", refreshThumb);
  mid.append(label, input);

  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost danger";
  remove.textContent = "Remove";
  remove.addEventListener("click", () => row.remove());

  row.append(thumb, mid, remove);
  imageListEl.appendChild(row);
  if (url) refreshThumb();
}

function updateThumb(node, url, idx) {
  if (!node.closest(".image-row")) return;

  if (!url) {
    const placeholder = document.createElement("div");
    placeholder.className = "thumb placeholder";
    placeholder.textContent = `图 ${idx}`;
    node.replaceWith(placeholder);
    return;
  }

  const img = document.createElement("img");
  img.className = "thumb";
  img.alt = `Image ${idx}`;
  img.src = url;
  img.onerror = () => {
    const placeholder = document.createElement("div");
    placeholder.className = "thumb placeholder";
    placeholder.textContent = "load fail";
    img.replaceWith(placeholder);
  };
  node.replaceWith(img);
}

function collectImageUrls() {
  return [...imageListEl.querySelectorAll('input[type="url"]')]
    .map((el) => el.value.trim())
    .filter(Boolean);
}

function autoFillFromPrompt({ silent = false } = {}) {
  const text = promptEl.value;
  if (!text.trim()) return { urls: [] };

  const { prompt: cleaned, urls } = extractUrlsLocally(text);
  if (!urls.length) {
    if (!silent) setStatus("未在提示词中识别到参考图 URL");
    return { urls: [] };
  }

  const key = urls.join("\n");
  if (key !== lastFilledUrlsKey) {
    setImageRows(urls);
    lastFilledUrlsKey = key;
  }

  if (cleaned && cleaned !== text.trim()) {
    promptEl.value = cleaned;
  }

  if (!silent) setStatus(`已自动填充 ${urls.length} 张参考图`);
  return { urls, cleaned };
}

function scheduleAutoParse() {
  if (parseTimer) clearTimeout(parseTimer);
  parseTimer = setTimeout(() => {
    if (currentMode !== "generate") return;
    if (!/https?:\/\//i.test(promptEl.value)) return;
    autoFillFromPrompt({ silent: true });
  }, 200);
}

async function loadHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    if (data.project_configured) {
      const proxy = data.active_proxy ? " · proxy on" : " · no proxy";
      healthEl.textContent = `GCP ready · ${data.model}${proxy}`;
      healthEl.className = "health ok";
    } else {
      healthEl.textContent = "Set GOOGLE_CLOUD_PROJECT in .env";
      healthEl.className = "health bad";
    }
  } catch {
    healthEl.textContent = "Server offline";
    healthEl.className = "health bad";
  }
}

// Each click cycles to the next sample so a walkthrough can show the range of
// instructions a mode handles, rather than the same line every time.
const MODE_EXAMPLES = {
  continue: [
    "Make the lighting warmer, like golden hour.",
    "Change the background to a snowy street at night.",
    "Remove the text on the screen.",
    "Make the camera slowly push in on her face.",
  ],
  edit: [
    "Remove the coffee cup from the table.",
    "Change her jacket to bright red.",
    "Make it rain lightly outside the window.",
    "Replace the daylight with neon-lit night lighting.",
  ],
  extend: [
    "Continue as the camera slowly pulls back to reveal the whole room.",
    "Continue as she turns toward the window and smiles.",
    "Continue as the lights dim and the screen powers down.",
    "Continue as he stands up and walks out of the frame.",
  ],
};
const exampleCursor = { continue: 0, edit: 0, extend: 0 };

async function pickSourceJob({ maxSeconds }) {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  return (data.jobs || []).find(
    (j) =>
      j.status === "completed" &&
      j.video_uri &&
      (maxSeconds == null || (j.duration_sec != null && j.duration_sec <= maxSeconds))
  );
}

async function loadGenerateExample() {
  const res = await fetch("/api/example");
  const data = await res.json();
  setMode("generate");
  promptEl.value = data.prompt;
  setImageRows(data.image_urls || []);
  document.getElementById("duration").value = String(data.duration || 8);
  document.getElementById("resolution").value = data.resolution || "720p";
  document.getElementById("aspectRatio").value = data.aspect_ratio || "16:9";
  document.getElementById("generateAudio").checked = !!data.generate_audio;
  document.getElementById("noSubtitles").checked = !!data.no_subtitles;
  document.getElementById("noBgm").checked = !!data.no_bgm;
  lastFilledUrlsKey = "";
  autoFillFromPrompt({ silent: false });
}

async function loadExample() {
  if (currentMode === "generate") {
    await loadGenerateExample();
    return;
  }

  const samples = MODE_EXAMPLES[currentMode];
  const idx = exampleCursor[currentMode] % samples.length;
  exampleCursor[currentMode] += 1;
  let text = samples[idx];
  if (currentMode !== "extend") text += " Keep everything else the same.";
  promptEl.value = text;

  if (currentMode === "extend") {
    document.getElementById("duration").value = "8";
  }

  // Edit-family calls cap the source at 10s; Extend accepts up to 30s.
  const maxSeconds = currentMode === "extend" ? 30 : 10;
  const source = await pickSourceJob({ maxSeconds });
  if (!source) {
    setStatus(
      `示例指令已填入。请先用 Generate 出一条 ${maxSeconds}s 以内的片子，再回到此模式选择源视频。`
    );
    return;
  }
  applySourceJob(source);
  setStatus(
    `示例已填入，源视频取自 job ${source.id}（${source.duration_sec ?? "?"}s）。`
  );
}

function showVideo(url) {
  stageEmpty.hidden = true;
  player.hidden = false;
  player.src = url;
  player.load();
}

function clearVideo() {
  player.hidden = true;
  player.removeAttribute("src");
  stageEmpty.hidden = false;
}

const MODE_CHIP = { edit: "Edit", extend: "Extend" };

function applySuggestion(job, suggestion) {
  applySourceJob(job);
  setMode(suggestion.mode);
  promptEl.value = suggestion.instruction;
  promptEl.focus();
  setStatus(
    `已采用建议：${suggestion.label}。源视频取自 job ${job.id}，点「${
      suggestion.mode === "edit" ? "Edit video" : "Extend video"
    }」开始。`
  );
}

async function renderSuggestions(job) {
  suggestionBubbles.innerHTML = "";
  suggestions.hidden = true;
  if (job.status !== "completed" || !job.video_uri) return;

  let items = [];
  try {
    const res = await fetch(`/api/jobs/${job.id}/suggestions`);
    if (!res.ok) return;
    items = (await res.json()).suggestions || [];
  } catch {
    return;
  }
  if (!items.length) return;

  for (const item of items) {
    const bubble = document.createElement("button");
    bubble.type = "button";
    bubble.className = "bubble";
    bubble.title = item.instruction;

    const mode = document.createElement("span");
    mode.className = "bubble-mode";
    mode.textContent = MODE_CHIP[item.mode] || item.mode;

    const text = document.createElement("span");
    text.className = "bubble-text";
    text.textContent = item.label;

    bubble.append(mode, text);
    bubble.addEventListener("click", () => applySuggestion(job, item));
    suggestionBubbles.appendChild(bubble);
  }

  suggestionsTitle.textContent =
    job.duration_sec > 10
      ? `下一步试试（当前 ${job.duration_sec}s，超过 10s 只能续写）`
      : "下一步试试（点击即可填入指令与源视频）";
  suggestions.hidden = false;
}

const CLIP_LABEL = {
  generate: "初次生成",
  continue: "继续编辑",
  edit: "编辑",
  extend: "续写",
};

function comparisonPlayers() {
  return Array.from(comparisonGrid.querySelectorAll("video"));
}

function clipCard({ title, subtitle, prompt, src, current }) {
  const card = document.createElement("figure");
  card.className = `clip-card${current ? " current" : ""}`;

  const head = document.createElement("figcaption");
  const name = document.createElement("strong");
  name.textContent = title;
  const meta = document.createElement("span");
  meta.textContent = subtitle;
  head.append(name, meta);

  const video = document.createElement("video");
  video.controls = true;
  video.playsInline = true;
  video.preload = "metadata";
  video.src = src;

  card.append(head, video);
  if (prompt) {
    const p = document.createElement("p");
    p.className = "clip-prompt";
    p.textContent = prompt;
    card.appendChild(p);
  }
  return card;
}

async function renderComparison(jobId) {
  let data;
  try {
    const res = await fetch(`/api/jobs/${jobId}/lineage`);
    if (!res.ok) throw new Error(await res.text());
    data = await res.json();
  } catch {
    comparison.hidden = true;
    return;
  }

  const clips = data.clips || [];
  // A lone clip has nothing to compare against.
  if (clips.length + (data.external_source ? 1 : 0) < 2) {
    comparison.hidden = true;
    comparisonGrid.innerHTML = "";
    return;
  }

  comparisonGrid.innerHTML = "";
  if (data.external_source) {
    comparisonGrid.appendChild(
      clipCard({
        title: "源视频",
        subtitle: "本次会话之外的素材",
        src: `/api/video?uri=${encodeURIComponent(data.external_source)}`,
      })
    );
  }
  clips.forEach((clip, idx) => {
    const label = CLIP_LABEL[clip.mode] || clip.mode;
    const length = clip.duration_sec ? `${clip.duration_sec}s` : "—";
    comparisonGrid.appendChild(
      clipCard({
        title: `${idx + 1}. ${label}`,
        subtitle: `${length} · ${clip.task || clip.mode}`,
        prompt: clip.prompt,
        src: clip.video_url,
        current: clip.is_current,
      })
    );
  });

  comparisonHint.textContent =
    `同一条素材衍生出 ${comparisonGrid.childElementCount} 个版本，按生成顺序排列，` +
    "高亮的是当前任务。点「同步播放」可一起播放对比。";
  comparison.hidden = false;
}

function renderJob(job) {
  selectedJob = job;
  jobIdEl.textContent = job.id ? `job ${job.id}` : "";
  jobLog.textContent = JSON.stringify(
    {
      status: job.status,
      message: job.message,
      mode: job.meta && job.meta.mode,
      task: job.meta && job.meta.task,
      interaction_id: job.interaction_id,
      video_uri: job.video_uri,
      error: job.error,
      meta: job.meta,
      updated_at: job.updated_at,
    },
    null,
    2
  );

  if (job.thoughts) {
    thoughtsBlock.hidden = false;
    thoughtsEl.textContent = job.thoughts;
  } else {
    thoughtsBlock.hidden = true;
  }

  const meta = job.meta || {};
  if (meta.final_prompt_preview) {
    promptBlock.hidden = false;
    promptSent.textContent = meta.final_prompt_preview;
    if (meta.prompt_enhanced) {
      const before = (meta.prompt_original || "").length;
      const after = meta.final_prompt_preview.length;
      promptDiffNote.textContent = `已自动润色（${meta.prompt_enhance_reason}）：只补了运镜与转场，${before} → ${after} 字符。`;
    } else if (meta.prompt_enhance_reason) {
      promptDiffNote.textContent = `未润色，使用原始提示词（${meta.prompt_enhance_reason}）。`;
    } else {
      promptDiffNote.textContent = "使用原始提示词。";
    }
  } else {
    promptBlock.hidden = true;
  }

  const canReuse = job.status === "completed" && Boolean(job.video_uri);
  resultActions.hidden = !(canReuse || job.reuse_ready);
  if (canReuse) applySourceJob(job);

  const regenBtn = document.getElementById("regenBtn");
  regenBtn.disabled = !job.reuse_ready;
  regenBtn.title = job.reuse_ready
    ? "复用该任务已上传到 GCS 的参考图，不再重新拉图"
    : "该任务没有可复用的 GCS 参考图";

  // Edit rejects anything longer than 10s; Extend still accepts up to 30s.
  const tooLongToEdit = job.duration_sec > 10;
  for (const id of ["useForContinue", "useForEdit"]) {
    const btn = document.getElementById(id);
    btn.disabled = tooLongToEdit;
    btn.title = tooLongToEdit
      ? `源视频 ${job.duration_sec}s 超过 Edit 的 10s 上限，请改用 Extend 或选择更短的片段`
      : "";
  }

  if (job.status === "completed" && job.video_url) {
    showVideo(job.video_url);
    setStatus(job.message || "Completed");
    renderComparison(job.id);
    renderSuggestions(job);
    generateBtn.disabled = false;
    testGcsBtn.disabled = false;
  } else if (job.status === "failed") {
    const short =
      (job.message || "").split("\n")[0] ||
      (job.error || "").split("\n")[0] ||
      "Failed";
    setStatus(short);
    generateBtn.disabled = false;
    testGcsBtn.disabled = false;
  } else {
    setStatus(job.message || job.status);
  }
}

async function refreshJobList() {
  const res = await fetch("/api/jobs");
  const data = await res.json();
  jobListEl.innerHTML = "";
  for (const job of data.jobs || []) {
    const li = document.createElement("li");
    const mode = job.mode || (job.meta && job.meta.mode) || "generate";
    const length = job.duration_sec ? ` · ${job.duration_sec}s` : "";
    const left = document.createElement("span");
    left.textContent = `${job.id} · ${mode}${length} · ${(job.message || job.status || "").slice(0, 30)}`;
    const tag = document.createElement("span");
    tag.className = `tag ${job.status}`;
    tag.textContent = job.status;
    li.append(left, tag);
    li.addEventListener("click", () => {
      renderJob(job);
      if (job.status === "completed" && job.video_url) showVideo(job.video_url);
      if (job.status === "completed") applySourceJob(job);
      if (job.status !== "completed" && job.status !== "failed") {
        startPolling(job.id);
      }
    });
    jobListEl.appendChild(li);
  }
}

function startPolling(jobId) {
  if (pollTimer) clearInterval(pollTimer);
  pollTimer = setInterval(async () => {
    try {
      const res = await fetch(`/api/jobs/${jobId}`);
      if (!res.ok) return;
      const job = await res.json();
      renderJob(job);
      await refreshJobList();
      if (job.status === "completed" || job.status === "failed") {
        clearInterval(pollTimer);
        pollTimer = null;
        generateBtn.disabled = false;
        testGcsBtn.disabled = false;
      }
    } catch (err) {
      setStatus(String(err));
    }
  }, 2500);
}

async function blobToBase64(blob) {
  const buffer = await blob.arrayBuffer();
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let i = 0; i < bytes.length; i += chunk) {
    binary += String.fromCharCode(...bytes.subarray(i, i + chunk));
  }
  return btoa(binary);
}

async function fetchReferenceImages(urls) {
  const out = [];
  const errors = [];
  for (let i = 0; i < urls.length; i++) {
    const url = urls[i];
    setStatus(`正在读取参考图 ${i + 1}/${urls.length}…`);
    try {
      const res = await fetch(url, { mode: "cors", cache: "force-cache" });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const blob = await res.blob();
      let mime = blob.type || "";
      if (!mime || mime === "application/octet-stream") {
        if (url.toLowerCase().includes(".png")) mime = "image/png";
        else if (url.toLowerCase().match(/\.jpe?g/)) mime = "image/jpeg";
        else mime = "image/png";
      }
      const data = await blobToBase64(blob);
      out.push({ mime_type: mime, data });
    } catch (err) {
      errors.push(`图${i + 1}: ${err.message || err}`);
    }
  }
  return { images: out, errors };
}

async function prepareReferenceImages() {
  autoFillFromPrompt({ silent: true });

  let prompt = promptEl.value.trim();
  let imageUrls = collectImageUrls();

  const local = extractUrlsLocally(prompt);
  if (local.urls.length && !imageUrls.length) {
    imageUrls = local.urls;
  }
  if (local.urls.length && local.prompt) {
    prompt = local.prompt;
    promptEl.value = local.prompt;
  }

  if (!imageUrls.length) {
    throw new Error("请先添加或粘贴参考图 URL");
  }

  setStatus(`正在读取参考图… (0/${imageUrls.length})`);
  const fetched = await fetchReferenceImages(imageUrls);
  if (!fetched.images.length) {
    throw new Error(`参考图读取失败（${fetched.errors.join("; ")}）`);
  }
  if (fetched.errors.length) {
    setStatus(
      `已读取 ${fetched.images.length}/${imageUrls.length} 张，其余失败：${fetched.errors.join("; ")}`
    );
  }
  return { prompt, imageUrls, referenceImages: fetched.images };
}

async function testGcsUpload() {
  testGcsBtn.disabled = true;
  generateBtn.disabled = true;
  try {
    const { referenceImages } = await prepareReferenceImages();
    setStatus(`Uploading ${referenceImages.length} image(s) → local → GCS…`);
    const res = await fetch("/api/test-gcs-upload", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ reference_images: referenceImages }),
    });
    const job = await res.json();
    if (!res.ok) {
      throw new Error(job.detail || JSON.stringify(job));
    }
    renderJob(job);
    await refreshJobList();
    const gcs = (job.meta && job.meta.gcs_refs) || [];
    setStatus(`GCS OK · ${gcs.length} object(s) under the configured input prefix.`);
  } catch (err) {
    setStatus(String(err));
  } finally {
    testGcsBtn.disabled = false;
    generateBtn.disabled = false;
  }
}

async function regenerateFromJob(job) {
  if (!job.reuse_ready) {
    throw new Error("该任务没有可复用的 GCS 参考图");
  }
  setMode("generate");
  if (job.shot_list) promptEl.value = job.shot_list;

  generateBtn.disabled = true;
  testGcsBtn.disabled = true;
  clearVideo();
  comparison.hidden = true;
  suggestions.hidden = true;
  setStatus("重新生成：复用 GCS 参考图，不再重新上传…");

  const body = {
    prompt: (promptEl.value.trim() || job.shot_list || ""),
    mode: "generate",
    reuse_job_id: job.id,
    duration: Number(document.getElementById("duration").value),
    resolution: document.getElementById("resolution").value,
    aspect_ratio: document.getElementById("aspectRatio").value,
    generate_audio: document.getElementById("generateAudio").checked,
    no_subtitles: document.getElementById("noSubtitles").checked,
    no_bgm: document.getElementById("noBgm").checked,
    enhance_prompt: enhancePromptEl.checked,
    image_urls: [],
    reference_images: [],
  };
  const res = await fetch("/api/generate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const created = await res.json();
  if (!res.ok) {
    throw new Error(
      typeof created.detail === "string"
        ? created.detail
        : JSON.stringify(created.detail || created)
    );
  }
  renderJob(created);
  await refreshJobList();
  startPolling(created.id);
}

async function submitJob() {
  const prompt = promptEl.value.trim();
  if (!prompt) {
    setStatus("请填写提示词 / 编辑指令");
    return;
  }

  generateBtn.disabled = true;
  testGcsBtn.disabled = true;
  clearVideo();
  comparison.hidden = true;
  suggestions.hidden = true;

  try {
    const base = {
      prompt,
      mode: currentMode,
      duration: Number(document.getElementById("duration").value),
      resolution: document.getElementById("resolution").value,
      aspect_ratio: document.getElementById("aspectRatio").value,
      generate_audio: document.getElementById("generateAudio").checked,
      no_subtitles: document.getElementById("noSubtitles").checked,
      no_bgm: document.getElementById("noBgm").checked,
      image_urls: [],
      reference_images: [],
      enhance_prompt: enhancePromptEl.checked,
    };

    if (currentMode === "generate") {
      const prepared = await prepareReferenceImages().catch(async (err) => {
        autoFillFromPrompt({ silent: true });
        const p = promptEl.value.trim();
        if (!p) throw err;
        return { prompt: p, imageUrls: [], referenceImages: [] };
      });
      base.prompt = prepared.prompt || prompt;
      base.reference_images = prepared.referenceImages;
      setStatus(
        `Submitting generate… (${base.reference_images.length} refs → GCS → Omni)`
      );
    } else {
      base.source_job_id = sourceJobIdEl.value.trim() || null;
      base.source_video_uri = sourceVideoUriEl.value.trim() || null;
      if (!base.source_video_uri && !base.source_job_id) {
        throw new Error(`${currentMode} 需要提供 gs:// 源视频，或选择一条已完成的任务`);
      }
      setStatus(`Submitting ${currentMode}…`);
    }

    const res = await fetch("/api/generate", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(base),
    });
    const job = await res.json();
    if (!res.ok) {
      throw new Error(
        typeof job.detail === "string" ? job.detail : JSON.stringify(job.detail || job)
      );
    }
    renderJob(job);
    await refreshJobList();
    startPolling(job.id);
  } catch (err) {
    setStatus(String(err));
    generateBtn.disabled = false;
    testGcsBtn.disabled = false;
  }
}

document.querySelectorAll(".mode-tab").forEach((btn) => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});

document.getElementById("playAllBtn").addEventListener("click", () => {
  for (const v of comparisonPlayers()) {
    v.currentTime = 0;
    v.muted = true;
    v.play().catch(() => {});
  }
  setStatus("同步播放中（对比时已静音，单独播放可听声音）");
});
document.getElementById("resetAllBtn").addEventListener("click", () => {
  for (const v of comparisonPlayers()) {
    v.pause();
    v.currentTime = 0;
  }
});

document.getElementById("regenBtn").addEventListener("click", () => {
  if (!selectedJob) return;
  regenerateFromJob(selectedJob).catch((e) => {
    setStatus(String(e));
    generateBtn.disabled = false;
    testGcsBtn.disabled = false;
  });
});

document.getElementById("useForContinue").addEventListener("click", () => {
  if (selectedJob) applySourceJob(selectedJob);
  setMode("continue");
  if (!promptEl.value.trim()) {
    promptEl.value = "Make the lighting warmer. Keep everything else the same.";
  }
  setStatus("已填入源视频 URI，请填写修改指令后提交 Continue edit");
});
document.getElementById("useForEdit").addEventListener("click", () => {
  if (selectedJob) applySourceJob(selectedJob);
  setMode("edit");
  if (!promptEl.value.trim()) {
    promptEl.value = "Remove the glowing tablet. Keep everything else the same.";
  }
  setStatus("已填入源视频 URI，请填写修改说明后提交 Edit video");
});
document.getElementById("useForExtend").addEventListener("click", () => {
  if (selectedJob) applySourceJob(selectedJob);
  setMode("extend");
  if (!promptEl.value.trim()) {
    promptEl.value = "Continue as they smile and the interface finishes upgrading.";
  }
  setStatus("已填入源视频 URI，请描述后续画面内容后提交 Extend");
});

document.getElementById("addImage").addEventListener("click", () => addImageRow());
document.getElementById("loadExample").addEventListener("click", () => {
  loadExample().catch((e) => setStatus(String(e)));
});
generateBtn.addEventListener("click", () => {
  submitJob().catch((e) => {
    setStatus(String(e));
    generateBtn.disabled = false;
    testGcsBtn.disabled = false;
  });
});
testGcsBtn.addEventListener("click", () => {
  testGcsUpload().catch((e) => {
    setStatus(String(e));
    testGcsBtn.disabled = false;
    generateBtn.disabled = false;
  });
});

promptEl.addEventListener("paste", () => {
  setTimeout(() => {
    if (currentMode === "generate") autoFillFromPrompt({ silent: false });
  }, 0);
});
promptEl.addEventListener("input", scheduleAutoParse);
promptEl.addEventListener("blur", () => {
  if (currentMode === "generate") autoFillFromPrompt({ silent: true });
});

setMode("generate");
addImageRow();
loadHealth();
refreshJobList().catch(() => {});
