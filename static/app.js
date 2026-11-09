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
const brandSubEl = document.getElementById("brandSub");
const stageEmptyCopyEl = document.getElementById("stageEmptyCopy");
const advancedHintEl = document.getElementById("advancedHint");
const guideTitle = document.getElementById("guideTitle");
const guideTask = document.getElementById("guideTask");
const guideSteps = document.getElementById("guideSteps");
const guideNote = document.getElementById("guideNote");
const exampleChips = document.getElementById("exampleChips");
const exampleRow = document.getElementById("exampleRow");
const promptField = document.getElementById("promptField");
const genTabs = document.getElementById("genTabs");
const sourceBox = document.getElementById("sourceBox");
const refsSection = document.getElementById("refsSection");
const refsTitle = document.getElementById("refsTitle");
const refsHint = document.getElementById("refsHint");
const addImageBtn = document.getElementById("addImage");
const uploadImagesBtn = document.getElementById("uploadImages");
const filePicker = document.getElementById("filePicker");
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
const enhanceHint = document.getElementById("enhanceHint");
const promptBlock = document.getElementById("promptBlock");
const promptSent = document.getElementById("promptSent");
const promptDiffNote = document.getElementById("promptDiffNote");
const suggestions = document.getElementById("suggestions");
const suggestionBubbles = document.getElementById("suggestionBubbles");
const suggestionsTitle = document.getElementById("suggestionsTitle");
const comparison = document.getElementById("comparison");
const comparisonGrid = document.getElementById("comparisonGrid");
const comparisonHint = document.getElementById("comparisonHint");
const modelSelect = document.getElementById("modelSelect");
const customModelField = document.getElementById("customModelField");
const customModelEl = document.getElementById("customModel");
const modelNote = document.getElementById("modelNote");
const seedEl = document.getElementById("seed");
const labelsEl = document.getElementById("labels");
const statefulEl = document.getElementById("stateful");
const statefulField = document.getElementById("statefulField");
const seedField = document.getElementById("seedField");
const labelsField = document.getElementById("labelsField");
const videoRefs = document.getElementById("videoRefs");
const videoListEl = document.getElementById("videoList");
const videoPicker = document.getElementById("videoPicker");
const durationEl = document.getElementById("duration");
const audioInputNote = document.getElementById("audioInputNote");

let catalog = [];
const videoPayloads = new WeakMap();
const FALLBACK_12 = {
  id: "gemini-omni-1.2-flash",
  generate_min: 3,
  generate_max: 15,
  frames_max: 10,
  edit_max: 15,
  extend_min: 3,
  extend_max: 10,
  extend_input_max: null,
  upscale_max: 15,
  reference_videos_max: 3,
  edit_reference_images_max: 2,
  supports_seed: true,
  supports_labels: true,
  supports_multiturn: true,
  supports_upscale: true,
};

function selectedModelId() {
  if (modelSelect.value === "custom") return customModelEl.value.trim() || FALLBACK_12.id;
  return modelSelect.value;
}

function activeProfile() {
  const id = selectedModelId();
  const known = catalog.find((item) => item.id === id);
  if (known) return known;
  if (id.includes("1.1")) {
    return {
      ...FALLBACK_12,
      id,
      generate_max: 10,
      edit_max: 10,
      upscale_max: null,
      supports_upscale: false,
      supports_seed: false,
      supports_labels: false,
      supports_multiturn: false,
      edit_reference_images_max: 0,
      extend_input_max: 30,
    };
  }
  return { ...FALLBACK_12, id };
}

function fillDuration(min, max, preferred) {
  const current = Number(durationEl.value || preferred || 8);
  durationEl.innerHTML = "";
  for (let n = min; n <= max; n += 1) {
    const opt = document.createElement("option");
    opt.value = String(n);
    opt.textContent = `${n}s`;
    durationEl.appendChild(opt);
  }
  const next = current >= min && current <= max ? current : preferred;
  durationEl.value = String(next);
}

function applyDurationForMode() {
  const profile = activeProfile();
  if (currentMode === "extend") {
    fillDuration(profile.extend_min, profile.extend_max, 8);
  } else if (currentMode === "generate" && currentGenTask === "frames_to_video") {
    fillDuration(profile.generate_min, Math.min(profile.generate_max, profile.frames_max), 8);
  } else {
    fillDuration(profile.generate_min, profile.generate_max, 8);
  }
}

let healthState = { ok: false, proxy: "", offline: false };

function profileHeadline(profile) {
  const name = profile.id.includes("1.1")
    ? "1.1 Preview"
    : profile.id.includes("1.2")
      ? "1.2 Flash"
      : profile.id;
  const extendIn =
    profile.extend_input_max == null
      ? "extend any-length in"
      : `extend input ≤${profile.extend_input_max}s`;
  const upscale = profile.supports_upscale ? " · upscale" : "";
  return `${name} · gen/edit ${profile.generate_min}–${profile.generate_max}s · ${extendIn} → ~40s out${upscale}`;
}

function modelNoteText(profile) {
  const extend =
    profile.extend_input_max == null
      ? "续写源片可为任意长度"
      : `续写源片要求 ≤ ${profile.extend_input_max} 秒`;
  const refs = profile.edit_reference_images_max
    ? `编辑可附最多 ${profile.edit_reference_images_max} 张参考图。`
    : "编辑不接受参考图。";
  const extras = [
    profile.supports_multiturn ? "可多轮编辑" : "每次编辑重传 gs://",
    profile.supports_upscale ? "可单独放大" : "无单独放大",
    profile.supports_seed ? "可发送 seed / labels" : "不发送 seed / labels",
  ].join("，");
  return (
    `${profile.label || profile.id}：生成 ${profile.generate_min}–${profile.generate_max} 秒` +
    `（首尾帧最长 ${profile.frames_max}），编辑最长 ${profile.edit_max} 秒。` +
    `${extend}，单次只加 ${profile.extend_min}–${profile.extend_max} 秒，成片约 ≤ 40 秒。${refs}${extras}。`
  );
}

function paintHealth() {
  if (!healthEl) return;
  if (healthState.offline) {
    healthEl.textContent = "Server offline";
    healthEl.className = "health bad";
    return;
  }
  if (!healthState.ok) {
    healthEl.textContent = "Set GOOGLE_CLOUD_PROJECT in .env";
    healthEl.className = "health bad";
    return;
  }
  healthEl.textContent = `GCP ready · ${selectedModelId()}${healthState.proxy}`;
  healthEl.className = "health ok";
}

function applyCapabilityChrome() {
  const profile = activeProfile();
  customModelField.hidden = modelSelect.value !== "custom";
  const upscaleBtn = document.querySelector('#modeTabs [data-mode="upscale"]');
  if (upscaleBtn) {
    upscaleBtn.disabled = !profile.supports_upscale;
    upscaleBtn.title = profile.supports_upscale ? "" : "当前模型没有单独放大";
  }
  seedField.hidden = !profile.supports_seed;
  labelsField.hidden = !profile.supports_labels;
  const chainable = profile.supports_multiturn && currentMode !== "generate" && currentMode !== "upscale";
  statefulField.hidden = !chainable;
  modelNote.textContent = modelNoteText(profile);
  if (brandSubEl) brandSubEl.textContent = profileHeadline(profile);
  if (advancedHintEl) {
    advancedHintEl.textContent = profile.supports_seed
      ? "Seed 与 labels 会随本次请求发送。多轮编辑需要上一条任务的 interaction id，服务端大约保留 7 天。"
      : "当前模型不发送 seed、labels，也不支持多轮 interaction。编辑和续写都会重传 gs:// 源片。";
  }
  if (stageEmptyCopyEl) {
    stageEmptyCopyEl.textContent = profile.supports_upscale
      ? "先选模型，再选文生 / 首帧 / 首尾帧 / 参考。成片之后可以继续编辑、续写，或把 360p / 720p 草稿放大。"
      : "先选模型，再选文生 / 首帧 / 首尾帧 / 参考。成片之后可以继续编辑或续写。当前模型没有单独放大。";
  }
  paintHealth();
  if (audioInputNote) audioInputNote.hidden = currentMode !== "generate";
}

function editGuideNote(profile) {
  const chain = profile.supports_multiturn
    ? "可勾选多轮编辑，不必重传成片。"
    : "每次编辑重传 gs://。";
  const refs = profile.edit_reference_images_max
    ? `可附最多 ${profile.edit_reference_images_max} 张参考图。`
    : "不接受参考图。";
  return `不能改配音或台词。当前模型最长编辑 ${profile.edit_max} 秒。${chain}${refs}`;
}

function extendInputSummary(profile) {
  if (profile.extend_input_max == null) {
    return (
      "1.2：源片可为任意长度（官方合同，Demo 不裁短）。模型只「看」最后约 10 秒当上下文；" +
      "成片里只「留」源片最后约 30 秒再接上新的 3–10 秒（不是 15 秒），总长约 ≤ 40 秒。更早的画面会被裁掉。" +
      "Duration 写入 response_format，表示本次追加秒数。"
    );
  }
  return (
    `1.1 / 本 Demo：源片必须 ≤ ${profile.extend_input_max} 秒。` +
    "单次新增 3–10 秒；成片约 ≤ 40 秒（末约 30 秒源片 + 新段）。要接更长源片请切到 Omni 1.2。"
  );
}

function parseLabels(text) {
  const labels = {};
  for (const part of text.split(",")) {
    const piece = part.trim();
    if (!piece) continue;
    const eq = piece.indexOf("=");
    if (eq <= 0) throw new Error("Labels 写成 key=value，多个用逗号隔开");
    labels[piece.slice(0, eq).trim()] = piece.slice(eq + 1).trim();
  }
  return labels;
}

function requestExtras() {
  const profile = activeProfile();
  const body = { model: selectedModelId(), stateful: false };
  if (profile.supports_seed && seedEl.value !== "") body.seed = Number(seedEl.value);
  if (profile.supports_labels && labelsEl.value.trim()) body.labels = parseLabels(labelsEl.value);
  if (profile.supports_multiturn) body.stateful = statefulEl.checked;
  return body;
}

let pollTimer = null;
let parseTimer = null;
let lastFilledUrlsKey = "";
let currentMode = "generate";
let currentGenTask = "text_to_video";
let serverExamples = null;

function generateExamples(kind) {
  return (serverExamples && serverExamples.generate && serverExamples.generate[kind]) || [];
}

function modeExamples(mode) {
  const list = serverExamples && serverExamples[mode];
  return Array.isArray(list) ? list : [];
}

async function ensureExamples() {
  if (serverExamples) return serverExamples;
  const res = await fetch("/api/examples");
  if (!res.ok) throw new Error("示例列表加载失败");
  serverExamples = await res.json();
  renderExampleChips();
  return serverExamples;
}
let selectedJob = null;
const rowPayloads = new WeakMap();

const GEN_GUIDE = {
  text_to_video: {
    title: "Text → video — 只靠文字生成",
    task: "task=text_to_video",
    steps: [
      "不需要图片。写清主体、动作、镜头（景别/焦距/运镜）、光线和风格。",
      "对白写在引号里；没有台词就写明 No dialogue。",
      "点下方示例可直接填入提示词，再按 Generate。",
    ],
    note: "适合没有角色一致性要求的镜头。要锁人物外观请改用 References。",
    placeholder: "例：A red paper lantern lifts off a wooden table at dusk. Slow tilt up, 24mm.",
    promptLabel: "Prompt（纯文本分镜）",
    button: "Generate from text",
    refsTitle: "",
    refsHint: "",
    enhance:
      "无参考图：允许把分镜写得更有镜头感。不会引入新角色或新剧情。",
    slots: 0,
  },
  image_to_video: {
    title: "First frame — 静帧当作第一帧",
    task: "task=image_to_video",
    steps: [
      "上传一张本地图，或粘贴公网 URL。这张图是成片的第 0 帧，不是「长得像」的参考。",
      "提示词只写从这一帧开始怎么动：推镜、刮风、光线变化。不要重写人物长相。",
      "点示例会填入一张公开静帧，可随时换成自己的图。",
    ],
    note: "图即首帧。若只要「长得像这张图」而不当第一帧，请用 References。",
    placeholder: "Use this image as the first frame. The camera slowly pushes in…",
    promptLabel: "Prompt（从首帧开始的动作）",
    button: "Generate from first frame",
    refsTitle: "First frame",
    refsHint: "一张图：本地上传或 URL。它会成为视频的第一帧。",
    enhance: "不改静帧外观，只补「必须发生的动作 / 运镜」。",
    slots: 1,
    slotLabels: ["首帧"],
  },
  frames_to_video: {
    title: "First + last — 两帧之间插值",
    task: "task=image_to_video（两张图）",
    steps: [
      "准备两张图：第一张是开场，第二张是收束。顺序不能反。",
      "提示词描述两帧之间的运镜（dolly、orbit、pan），而不是新的剧情。",
      "适合环绕、推拉、近似循环的镜头。",
    ],
    note: "API 仍是 image_to_video。第一张=first frame，第二张=last frame。",
    placeholder: "Start on the first image and end on the second. Slow dolly between them…",
    promptLabel: "Prompt（两帧之间怎么走）",
    button: "Generate between frames",
    refsTitle: "First & last frames",
    refsHint: "必须两张。可本地上传或 URL。上面是首帧，下面是尾帧。",
    enhance: "不改两帧外观，只强调中间必须看到的运动。",
    slots: 2,
    slotLabels: ["首帧", "尾帧"],
  },
  reference_to_video: {
    title: "References — 参考图锁定人物与画风",
    task: "task=reference_to_video",
    steps: [
      "上传或粘贴 1 张及以上参考图。它们不是第一帧，只锁定长相、服装、场景、画风。",
      "分镜里用 @图片1、@图片2 绑定角色。不要再用文字重写五官。",
      "提示词里若带 参考图：[\"https://…\"] 会自动填入列表。",
    ],
    note: "服务端会把 @图片N / @视频N 改成 <IMAGE_REF_N> / <VIDEO_REF_N>。参考视频不当源片剪辑，其中的声音会被忽略。",
    placeholder: "粘贴分镜；用 @图片1 绑定角色。也可在下方上传本地图。",
    promptLabel: "Prompt（分镜 + @图片N）",
    button: "Generate from references",
    refsTitle: "Reference images",
    refsHint: "本地上传或公网 URL。图是参考，不是字面第一帧。",
    enhance: "分镜原文不改，只追加动作/运镜，避免把人物画风写崩。",
    slots: "n",
  },
};

const MODE_GUIDE = {
  continue: {
    title: "Continue edit — 基于上一条结果再改一刀",
    task: "task=edit",
    steps: [
      "在右侧选一条已完成成片，或点「以此结果继续编辑」。",
      "指令要短：改光、去掉一个物体、换一件衣服。一次只改一件事。",
      "可选「自动润色」才会补 Keep everything else；默认原样提交。",
    ],
    note: "源片长度不能超过当前模型的编辑上限。1.2 可勾选多轮编辑，不必重传成片。不能改配音。",
    placeholder: "例：Make the lighting warmer. Keep everything else the same.",
    promptLabel: "Edit instruction（简短指令）",
    button: "Continue edit",
  },
  edit: {
    title: "Edit video — 对指定视频做一次局部改动",
    task: "task=edit",
    steps: [
      "填入已完成任务，或粘贴任意 gs:// 源视频。",
      "同样是短指令；与 Continue 调用同一接口，只是源视频填写方式不同。",
    ],
    note: "不能改配音或台词。1.2 最长编辑 15 秒，并可附最多 2 张参考图。1.1 最长 10 秒。",
    placeholder: "例：Remove the tablet from her hands. Keep everything else the same.",
    promptLabel: "Edit instruction",
    button: "Edit video",
  },
  extend: {
    title: "Extend — 从片尾往后接",
    task: "task=extend",
    steps: [
      "选择源视频。描述接下来几秒发生什么，不要重述原片。",
      "Duration 是本次新增长度（3–10s，不是 15s）。返回拼接后的完整成片。",
      "例如 8s 源片再续 8s → 约 16s。不要按 15+15+15 规划到 45s。",
      "「看约 10 秒」是上下文；「留约 30 秒」是成片里保留的源片末尾。两件事不一样。",
    ],
    note: "",
    placeholder: "例：Continue as they watch the interface light up and smile.",
    promptLabel: "What happens next",
    button: "Extend video",
    enhance: "续写指令会略作镜头/节奏润色，不改人物和画风。",
  },
  upscale: {
    title: "Upscale — 只提高分辨率",
    task: "task=upscale",
    steps: [
      "选一条不超过 15 秒的成片，或粘贴 gs:// 源视频。",
      "把 Resolution 选得比源片更高。官方建议先用 360p / 720p 出草稿，再放大到 1080p 或 4K。",
      "不要写提示词。线上会拒绝任何文字：“Text input is not supported for upscale task.”",
    ],
    note: "只在 Omni 1.2。请求里只有源视频和目标分辨率，不能改内容、运镜或对白。这不是 Cloud Transcoder 的转码。",
    placeholder: "",
    promptLabel: "Prompt",
    button: "Upscale video",
  },
};

function currentGuide() {
  if (currentMode === "generate") return GEN_GUIDE[currentGenTask];
  return MODE_GUIDE[currentMode];
}

function setStatus(text) {
  statusLine.textContent = text;
}

function renderExampleChips() {
  exampleChips.innerHTML = "";
  if (exampleRow) exampleRow.hidden = false;
  const items =
    currentMode === "generate"
      ? generateExamples(currentGenTask).map((item, i) => ({
          label: item.label,
          apply: () => applyGenerateExample(currentGenTask, i),
        }))
      : modeExamples(currentMode).map((text, i) => ({
          label: String(text).replace(/^Continue as /i, "").slice(0, 18),
          apply: () => applyModeExample(currentMode, i),
        }));
  for (const item of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "example-chip";
    btn.textContent = item.label;
    btn.addEventListener("click", () => {
      item.apply();
      exampleChips.querySelectorAll(".example-chip").forEach((el) => {
        el.classList.toggle("active", el === btn);
      });
    });
    exampleChips.appendChild(btn);
  }
  if (exampleRow) exampleRow.hidden = items.length === 0;
}

function applyGuide() {
  const g = currentGuide();
  guideTitle.textContent = g.title;
  guideTask.innerHTML = `<code>${g.task}</code>`;
  guideSteps.innerHTML = "";
  for (const step of g.steps) {
    const li = document.createElement("li");
    li.textContent = step;
    guideSteps.appendChild(li);
  }
  guideNote.textContent =
    currentMode === "extend"
      ? extendInputSummary(activeProfile())
      : currentMode === "continue" || currentMode === "edit"
        ? editGuideNote(activeProfile())
        : g.note;
  promptLabel.textContent = g.promptLabel;
  promptEl.placeholder = g.placeholder;
  generateBtn.textContent = g.button;
  if (enhanceHint && g.enhance) enhanceHint.textContent = g.enhance;
  renderExampleChips();
}

function syncImageSlots() {
  const g = GEN_GUIDE[currentGenTask];
  if (!g || currentMode !== "generate") return;
  const want = g.slots;
  if (want === 0) return;
  if (want === "n") {
    if (!imageListEl.children.length) addImageRow({ label: "@图片1" });
    return;
  }
  const labels = g.slotLabels || [];
  while (imageListEl.children.length > want) {
    imageListEl.lastElementChild.remove();
  }
  while (imageListEl.children.length < want) {
    addImageRow({ label: labels[imageListEl.children.length] || "图" });
  }
  [...imageListEl.children].forEach((row, i) => {
    const lab = row.querySelector(".idx");
    if (lab) lab.textContent = labels[i] || `图 ${i + 1}`;
    const remove = row.querySelector("[data-action=remove]");
    if (remove) remove.hidden = true;
  });
}

function setGenTask(kind) {
  currentGenTask = kind;
  genTabs.querySelectorAll("[data-gen]").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.gen === kind);
  });
  const isGenerate = currentMode === "generate";
  genTabs.hidden = !isGenerate;
  const g = GEN_GUIDE[kind];
  const showRefs = isGenerate && g.slots !== 0;
  refsSection.hidden = !showRefs;
  testGcsBtn.hidden = !showRefs;
  addImageBtn.hidden = g.slots !== "n";
  uploadImagesBtn.hidden = !showRefs;
  if (showRefs) {
    refsTitle.textContent = g.refsTitle;
    refsHint.textContent = g.refsHint;
    filePicker.multiple = g.slots === "n" || g.slots === 2;
    syncImageSlots();
  }
  const showVideos =
    isGenerate && kind === "reference_to_video" && activeProfile().reference_videos_max > 0;
  videoRefs.hidden = !showVideos;
  applyGuide();
  applyDurationForMode();
  applyCapabilityChrome();
}

const MODE_STARTER = {
  continue: "Make the lighting warmer. Keep everything else the same.",
  edit: "A slow push-in. Keep everything else the same.",
  extend: "Continue as the camera slowly pulls back on the same scene.",
  upscale: "",
};
const draftPrompt = {};
const draftResolution = {};
const draftDuration = {};
const imageStash = {};

function imageStashKey(mode) {
  if (mode === "generate") return "generate";
  if (mode === "continue" || mode === "edit" || mode === "extend") return "followup";
  return null;
}

function stashImages(key) {
  const frag = document.createDocumentFragment();
  while (imageListEl.firstChild) frag.appendChild(imageListEl.firstChild);
  imageStash[key] = frag;
}

function restoreImages(key) {
  imageListEl.replaceChildren();
  const frag = imageStash[key];
  if (frag) imageListEl.appendChild(frag);
}

function rememberPrompt(mode) {
  if (!mode) return;
  if (mode !== "upscale") draftPrompt[mode] = promptEl.value;
  draftResolution[mode] = document.getElementById("resolution").value;
  draftDuration[mode] = durationEl.value;
}

function restorePrompt(mode) {
  if (mode === "upscale") {
    promptEl.value = "";
  } else if (typeof draftPrompt[mode] === "string") {
    promptEl.value = draftPrompt[mode];
    if (!promptEl.value.trim() && MODE_STARTER[mode]) promptEl.value = MODE_STARTER[mode];
  } else {
    promptEl.value = MODE_STARTER[mode] || "";
  }
  const resolution = draftResolution[mode];
  const resolutionEl = document.getElementById("resolution");
  if (resolution && [...resolutionEl.options].some((opt) => opt.value === resolution)) {
    resolutionEl.value = resolution;
  }
  const duration = draftDuration[mode];
  if (duration && [...durationEl.options].some((opt) => opt.value === duration)) {
    durationEl.value = duration;
  }
}

function raiseLowUpscaleResolution() {
  const resolution = document.getElementById("resolution");
  if (resolution.value === "360p" || resolution.value === "720p") {
    resolution.value = "1080p";
  }
}

function setMode(mode) {
  if (mode === "upscale" && !activeProfile().supports_upscale) {
    mode = "generate";
    setStatus("当前模型没有单独放大，已回到 Generate。");
  }
  const previousMode = currentMode;
  const switching = previousMode !== mode;
  if (switching) {
    rememberPrompt(previousMode);
    const fromKey = imageStashKey(previousMode);
    const toKey = imageStashKey(mode);
    if (fromKey && fromKey !== toKey) stashImages(fromKey);
    if (toKey && fromKey !== toKey) restoreImages(toKey);
    if (!toKey) imageListEl.replaceChildren();
  }
  currentMode = mode;
  document.querySelectorAll("#modeTabs .mode-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });

  const isGenerate = mode === "generate";
  sourceBox.hidden = isGenerate;
  sourceVideoField.hidden = isGenerate;
  audioToggles.hidden = !isGenerate;
  durationField.hidden = mode === "continue" || mode === "edit" || mode === "upscale";
  // Official extend uses response_format.duration; resolution follows the source.
  resolutionField.hidden = mode === "extend";
  aspectField.hidden = !isGenerate;
  if (promptField) promptField.hidden = mode === "upscale";
  enhanceToggle.hidden = !(isGenerate || mode === "extend");

  if (isGenerate) {
    setGenTask(currentGenTask);
  } else {
    genTabs.hidden = true;
    videoRefs.hidden = true;
    const editRefs =
      activeProfile().edit_reference_images_max > 0 && mode !== "upscale";
    refsSection.hidden = !editRefs;
    testGcsBtn.hidden = true;
    addImageBtn.hidden = !editRefs;
    uploadImagesBtn.hidden = !editRefs;
    if (editRefs) {
      refsTitle.textContent = "可选参考图";
      refsHint.textContent = `最多 ${activeProfile().edit_reference_images_max} 张，可选。只有指令里写了 @图片1 才会上传；公网地址由服务端下载。`;
      filePicker.multiple = true;
      if (!imageListEl.children.length) addImageRow({ label: "@图片1" });
    }
    applyGuide();
    applyDurationForMode();
    applyCapabilityChrome();
  }
  if (switching) {
    restorePrompt(mode);
    if (!isGenerate && selectedJob && selectedJob.video_uri) applySourceJob(selectedJob);
    if (mode === "upscale") raiseLowUpscaleResolution();
  }
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

function setImageRows(urls, { labels } = {}) {
  imageListEl.innerHTML = "";
  const list = urls && urls.length ? urls : [""];
  list.forEach((url, i) => {
    addImageRow({
      url,
      label: (labels && labels[i]) || `@图片${i + 1}`,
      removable: currentGenTask === "reference_to_video",
    });
  });
}

function addImageRow({ url = "", label = "", removable = true, file = null } = {}) {
  const idx = imageListEl.children.length + 1;
  const row = document.createElement("div");
  row.className = "image-row";

  const thumb = document.createElement("div");
  thumb.className = "thumb placeholder";
  thumb.textContent = label || `图 ${idx}`;

  const mid = document.createElement("div");
  mid.className = "mid";
  const idxEl = document.createElement("div");
  idxEl.className = "idx";
  idxEl.textContent = label || `@图片${idx}`;
  const input = document.createElement("input");
  input.type = "url";
  input.placeholder = "https://… image URL";
  input.value = url;
  const fileName = document.createElement("p");
  fileName.className = "file-name";
  fileName.hidden = true;
  const refreshThumb = () => updateThumb(row, input.value.trim(), idxEl.textContent);
  input.addEventListener("change", () => {
    rowPayloads.delete(row);
    fileName.hidden = true;
    refreshThumb();
  });
  input.addEventListener("blur", refreshThumb);
  mid.append(idxEl, input, fileName);

  const actions = document.createElement("div");
  actions.className = "row-actions";
  const upload = document.createElement("button");
  upload.type = "button";
  upload.className = "ghost";
  upload.textContent = "Upload";
  upload.addEventListener("click", () => pickFilesForRow(row));
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost danger";
  remove.dataset.action = "remove";
  remove.textContent = "Remove";
  remove.hidden = !removable;
  remove.addEventListener("click", () => {
    if (imageListEl.children.length <= 1 && currentGenTask !== "reference_to_video") {
      return;
    }
    row.remove();
    relabelReferenceRows();
  });
  actions.append(upload, remove);

  row.append(thumb, mid, actions);
  imageListEl.appendChild(row);
  if (file) applyFileToRow(row, file);
  else if (url) refreshThumb();
}

function relabelReferenceRows() {
  if (currentGenTask !== "reference_to_video") return;
  [...imageListEl.children].forEach((row, i) => {
    const lab = row.querySelector(".idx");
    if (lab) lab.textContent = `@图片${i + 1}`;
  });
}

function updateThumb(row, url, label) {
  const node = row.querySelector(".thumb, img.thumb");
  if (!node) return;
  if (rowPayloads.get(row)?.preview) {
    const img = document.createElement("img");
    img.className = "thumb";
    img.alt = label;
    img.src = rowPayloads.get(row).preview;
    node.replaceWith(img);
    return;
  }
  if (!url) {
    const placeholder = document.createElement("div");
    placeholder.className = "thumb placeholder";
    placeholder.textContent = label || "图";
    node.replaceWith(placeholder);
    return;
  }
  const img = document.createElement("img");
  img.className = "thumb";
  img.alt = label;
  img.src = url;
  img.onerror = () => {
    const placeholder = document.createElement("div");
    placeholder.className = "thumb placeholder";
    placeholder.textContent = "load fail";
    img.replaceWith(placeholder);
  };
  node.replaceWith(img);
}

function pickFilesForRow(row) {
  const picker = document.createElement("input");
  picker.type = "file";
  picker.accept = filePicker.accept;
  picker.addEventListener("change", async () => {
    const file = picker.files && picker.files[0];
    if (file) await applyFileToRow(row, file);
  });
  picker.click();
}

async function applyFileToRow(row, file) {
  const payload = await fileToPayload(file);
  rowPayloads.set(row, payload);
  const input = row.querySelector('input[type="url"]');
  if (input) input.value = "";
  const nameEl = row.querySelector(".file-name");
  if (nameEl) {
    nameEl.hidden = false;
    nameEl.textContent = file.name;
  }
  updateThumb(row, "", row.querySelector(".idx")?.textContent || file.name);
}

async function fileToPayload(file) {
  const dataUrl = await new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result || ""));
    reader.onerror = () => reject(reader.error || new Error("read failed"));
    reader.readAsDataURL(file);
  });
  const comma = dataUrl.indexOf(",");
  const data = comma >= 0 ? dataUrl.slice(comma + 1) : dataUrl;
  const preview = URL.createObjectURL(file);
  return {
    mime_type: file.type || "image/png",
    data,
    name: file.name,
    preview,
  };
}

async function handleUploadFiles(fileList) {
  const files = [...fileList].filter((f) => f.type.startsWith("image/") || !f.type);
  if (!files.length) return;
  if (currentGenTask === "image_to_video") {
    const row = imageListEl.children[0] || addImageRow({ label: "首帧", removable: false });
    await applyFileToRow(row, files[0]);
    return;
  }
  if (currentGenTask === "frames_to_video") {
    syncImageSlots();
    if (files[0] && imageListEl.children[0]) await applyFileToRow(imageListEl.children[0], files[0]);
    if (files[1] && imageListEl.children[1]) await applyFileToRow(imageListEl.children[1], files[1]);
    return;
  }
  for (const file of files) {
    const empty = [...imageListEl.children].find(
      (row) => !rowPayloads.get(row) && !row.querySelector('input[type="url"]')?.value
    );
    if (empty) await applyFileToRow(empty, file);
    else addImageRow({ file, removable: true });
  }
  relabelReferenceRows();
}

function autoFillFromPrompt({ silent = false } = {}) {
  if (currentMode !== "generate" || currentGenTask !== "reference_to_video") {
    return { urls: [] };
  }
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
    if (currentMode !== "generate" || currentGenTask !== "reference_to_video") return;
    if (!/https?:\/\//i.test(promptEl.value)) return;
    autoFillFromPrompt({ silent: true });
  }, 200);
}

async function loadHealth() {
  try {
    const res = await fetch("/api/health");
    const data = await res.json();
    catalog = data.models || [];
    if (data.model && [...modelSelect.options].some((opt) => opt.value === data.model)) {
      modelSelect.value = data.model;
    }
    setMode(currentMode);
    if (data.project_configured) {
      healthState = { ok: true, proxy: data.active_proxy ? " · proxy on" : " · no proxy", offline: false };
    } else {
      healthState = { ok: false, proxy: "", offline: false };
    }
    paintHealth();
  } catch {
    healthState = { ok: false, proxy: "", offline: true };
    paintHealth();
  }
}

const exampleCursor = { continue: 0, edit: 0, extend: 0, upscale: 0, generate: 0 };

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

async function applyGenerateExample(kind, index) {
  await ensureExamples();
  setMode("generate");
  setGenTask(kind);
  const item = generateExamples(kind)[index];
  if (!item) return;
  promptEl.value = item.prompt || "";
  if (item.duration) document.getElementById("duration").value = String(item.duration);
  if (item.aspect_ratio) document.getElementById("aspectRatio").value = item.aspect_ratio;
  document.getElementById("resolution").value = item.resolution || "720p";
  const labels = GEN_GUIDE[kind].slotLabels;
  if (kind === "text_to_video") {
    imageListEl.innerHTML = "";
  } else {
    setImageRows(item.image_urls || [], { labels });
    if (kind === "reference_to_video" && !(item.image_urls || []).length) {
      addImageRow({ removable: true });
    }
    syncImageSlots();
  }
  lastFilledUrlsKey = "";
  setStatus(`已填入「${item.label || "示例"}」· ${GEN_GUIDE[kind].task}`);
}

async function applyModeExample(mode, index) {
  await ensureExamples();
  const samples = modeExamples(mode);
  if (!samples.length) return;
  let text = samples[index % samples.length];
  if (mode !== "extend" && mode !== "upscale" && !/keep everything else/i.test(text)) {
    text += " Keep everything else the same.";
  }
  promptEl.value = text;
  draftPrompt[mode] = promptEl.value;
  if (mode === "extend") document.getElementById("duration").value = "8";
  const profile = activeProfile();
  const maxSeconds =
    mode === "extend"
      ? profile.extend_input_max
      : mode === "upscale"
        ? profile.upscale_max
        : profile.edit_max;
  const currentUri = sourceVideoUriEl.value.trim();
  const currentId = sourceJobIdEl.value.trim();
  const currentTooLong =
    maxSeconds != null &&
    selectedJob &&
    selectedJob.duration_sec != null &&
    selectedJob.duration_sec > maxSeconds + 0.5 &&
    (selectedJob.id === currentId || selectedJob.video_uri === currentUri);
  if (currentTooLong) {
    setStatus(`示例已填入。当前源片 ${selectedJob.duration_sec}s 超过 ${maxSeconds}s，请换一条更短的成片。`);
    return;
  }
  if (currentUri.startsWith("gs://") || currentId) {
    setStatus("示例指令已填入，沿用当前源视频。");
    return;
  }
  const source = await pickSourceJob({ maxSeconds });
  if (!source) {
    const limit = maxSeconds == null ? "任意长度" : `${maxSeconds}s 以内`;
    setStatus(`示例指令已填入。请先 Generate 一条${limit}的片子，再选择源视频。`);
    return;
  }
  applySourceJob(source);
  setStatus(`示例已填入，源视频 job ${source.id}（${source.duration_sec ?? "?"}s）。`);
}

async function loadGenerateExample() {
  await ensureExamples();
  const list = generateExamples(currentGenTask);
  if (!list.length) {
    setStatus("这个能力还没有示例");
    return;
  }
  const idx = (exampleCursor.generate || 0) % list.length;
  exampleCursor.generate = idx + 1;
  await applyGenerateExample(currentGenTask, idx);
}

async function loadExample() {
  if (currentMode === "generate") {
    await loadGenerateExample();
    return;
  }
  await ensureExamples();
  const samples = modeExamples(currentMode);
  if (!samples.length) {
    setStatus("这个能力不使用提示词示例");
    return;
  }
  const idx = exampleCursor[currentMode] % samples.length;
  exampleCursor[currentMode] += 1;
  await applyModeExample(currentMode, idx);
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

const MODE_CHIP = { edit: "Edit", extend: "Extend", upscale: "Upscale" };

function applySuggestion(job, suggestion) {
  applySourceJob(job);
  setMode(suggestion.mode);
  promptEl.value = suggestion.instruction;
  draftPrompt[suggestion.mode] = suggestion.instruction;
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

  const editMax = job.edit_max_sec || activeProfile().edit_max;
  suggestionsTitle.textContent =
    job.duration_sec > editMax
      ? `下一步试试（当前 ${job.duration_sec}s，超过 ${editMax}s 只能续写）`
      : "下一步试试（点击即可填入指令与源视频）";
  suggestions.hidden = false;
}

const CLIP_LABEL = {
  generate: "初次生成",
  text_to_video: "文生视频",
  image_to_video: "首帧",
  frames_to_video: "首尾帧",
  reference_to_video: "参考",
  continue: "继续编辑",
  edit: "编辑",
  extend: "续写",
  upscale: "放大",
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
    const kind = clip.generate_task || clip.task || clip.mode;
    const label = CLIP_LABEL[kind] || CLIP_LABEL[clip.mode] || clip.mode;
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
    ? job.generate_task === "text_to_video"
      ? "按同一分镜再跑一次 text_to_video"
      : "复用该任务已上传到 GCS 的图片，不再重新上传"
    : "该任务没有可复用的图片或分镜";

  const editMax = job.edit_max_sec || activeProfile().edit_max;
  const tooLongToEdit = job.duration_sec != null && job.duration_sec > editMax + 0.05;
  for (const id of ["useForContinue", "useForEdit"]) {
    const btn = document.getElementById(id);
    btn.disabled = tooLongToEdit;
    btn.title = tooLongToEdit
      ? `源视频 ${job.duration_sec}s 超过 Edit 的 ${editMax}s 上限，请改用 Extend 或选择更短的片段`
      : "";
  }
  const upscaleBtn = document.getElementById("useForUpscale");
  const canUpscale =
    activeProfile().supports_upscale &&
    (job.duration_sec == null || job.duration_sec <= 15.5);
  upscaleBtn.disabled = !canUpscale;
  upscaleBtn.title = !activeProfile().supports_upscale
    ? "当前模型没有单独放大"
    : canUpscale
      ? "用 task=upscale 提高到更高分辨率。不能写提示词，也不能改内容"
      : "放大要求源片不超过 15 秒";

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
    const kind = job.generate_task || (job.meta && job.meta.generate_task) || mode;
    const length = job.duration_sec ? ` · ${job.duration_sec}s` : "";
    const left = document.createElement("span");
    left.textContent = `${job.id} · ${CLIP_LABEL[kind] || kind}${length} · ${(job.message || job.status || "").slice(0, 28)}`;
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

function collectImageRefs() {
  const refs = [];
  for (const row of [...imageListEl.children]) {
    const stored = rowPayloads.get(row);
    if (stored?.data) {
      refs.push({ mime_type: stored.mime_type || "image/png", data: stored.data });
      continue;
    }
    const url = row.querySelector('input[type="url"]')?.value.trim();
    if (!url) continue;
    if (!/^https?:\/\//i.test(url)) {
      throw new Error("参考图请上传文件，或粘贴 http(s) 地址");
    }
    refs.push({ url, mime_type: mimeFromUrl(url) });
  }
  return refs;
}

function promptCitesImages(text) {
  return /@图片\s*\d|@image\s*\d/i.test(text || "");
}

function mimeFromUrl(url) {
  const lower = url.toLowerCase();
  if (lower.includes(".png")) return "image/png";
  if (lower.match(/\.jpe?g/)) return "image/jpeg";
  if (lower.includes(".webp")) return "image/webp";
  if (lower.includes(".gif")) return "image/gif";
  return "image/jpeg";
}

async function prepareGenerateInput() {
  if (currentGenTask === "reference_to_video") {
    autoFillFromPrompt({ silent: true });
  }
  const prompt = promptEl.value.trim();
  if (currentGenTask === "text_to_video") {
    if (!prompt) throw new Error("请填写文生视频提示词");
    return { prompt, referenceImages: [] };
  }

  const images = collectImageRefs();
  const hasVideo = [...videoListEl.children].some((row) => {
    const stored = videoPayloads.get(row);
    const uri = row.querySelector("input")?.value.trim();
    return Boolean(stored?.file || uri);
  });
  const need = currentGenTask === "frames_to_video" ? 2 : 1;
  if (currentGenTask === "reference_to_video" && images.length === 0 && hasVideo) {
    return { prompt, referenceImages: [] };
  }
  if (images.length < need) {
    throw new Error(
      currentGenTask === "frames_to_video"
        ? "首尾帧需要两张图，可本地上传或粘贴 URL"
        : "请上传或粘贴至少一张图"
    );
  }
  return { prompt, referenceImages: images };
}

async function testGcsUpload() {
  testGcsBtn.disabled = true;
  generateBtn.disabled = true;
  try {
    const { referenceImages } = await prepareGenerateInput();
    if (!referenceImages.length) {
      throw new Error("没有可上传的图片");
    }
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
    throw new Error("该任务没有可复用的图片或分镜");
  }
  setMode("generate");
  const kind = job.generate_task || job.meta?.generate_task || "reference_to_video";
  if (GEN_GUIDE[kind]) setGenTask(kind);
  if (job.shot_list) promptEl.value = job.shot_list;

  generateBtn.disabled = true;
  testGcsBtn.disabled = true;
  clearVideo();
  comparison.hidden = true;
  suggestions.hidden = true;
  setStatus(
    kind === "text_to_video"
      ? "重新生成：沿用上次分镜…"
      : "重新生成：复用 GCS 图片，不再重新上传…"
  );

  const body = {
    prompt: (promptEl.value.trim() || job.shot_list || ""),
    mode: "generate",
    generate_task: kind,
    reuse_job_id: job.id,
    ...requestExtras(),
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

function addVideoRow({ uri = "", file = null } = {}) {
  const row = document.createElement("div");
  row.className = "image-row";
  const label = document.createElement("div");
  label.className = "idx";
  label.textContent = `视频 ${videoListEl.children.length + 1}`;
  const input = document.createElement("input");
  input.type = "text";
  input.placeholder = "gs://bucket/clip.mp4，或用 Upload clips";
  input.value = uri;
  const remove = document.createElement("button");
  remove.type = "button";
  remove.className = "ghost";
  remove.textContent = "移除";
  remove.addEventListener("click", () => {
    videoPayloads.delete(row);
    row.remove();
  });
  if (file) {
    input.value = file.name;
    input.readOnly = true;
    videoPayloads.set(row, { file });
  }
  row.append(label, input, remove);
  videoListEl.appendChild(row);
  return row;
}

async function collectReferenceVideos() {
  const videos = [];
  const rows = [...videoListEl.children];
  if (rows.length > 3) throw new Error("参考视频最多 3 段");
  for (const row of rows) {
    const stored = videoPayloads.get(row);
    if (stored?.file) {
      const data = await fileToPayload(stored.file);
      videos.push({
        mime_type: stored.file.type || "video/mp4",
        data: data.data,
      });
      continue;
    }
    const uri = row.querySelector("input")?.value.trim() || "";
    if (!uri) continue;
    if (!uri.startsWith("gs://")) {
      throw new Error("参考视频请上传文件，或粘贴 gs:// 地址");
    }
    videos.push({ mime_type: "video/mp4", uri });
  }
  return videos;
}

async function submitJob() {
  const prompt = promptEl.value.trim();
  if (!prompt && currentMode !== "upscale") {
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
      prompt: currentMode === "upscale" ? "" : prompt,
      mode: currentMode,
      duration: Number(durationEl.value),
      resolution: document.getElementById("resolution").value,
      aspect_ratio: document.getElementById("aspectRatio").value,
      generate_audio: document.getElementById("generateAudio").checked,
      no_subtitles: document.getElementById("noSubtitles").checked,
      no_bgm: document.getElementById("noBgm").checked,
      image_urls: [],
      reference_images: [],
      reference_videos: [],
      enhance_prompt: enhancePromptEl.checked,
      generate_task: currentMode === "generate" ? currentGenTask : undefined,
      ...requestExtras(),
    };

    if (currentMode === "generate") {
      const prepared = await prepareGenerateInput();
      base.prompt = prepared.prompt || prompt;
      base.reference_images = prepared.referenceImages;
      if (currentGenTask === "reference_to_video") {
        base.reference_videos = await collectReferenceVideos();
      }
      const n = base.reference_images.length;
      const v = base.reference_videos.length;
      setStatus(
        n || v
          ? `Submitting ${currentGenTask}… (${n} image(s), ${v} video(s) → GCS → Omni)`
          : `Submitting ${currentGenTask}…`
      );
    } else if (currentMode !== "upscale" && activeProfile().edit_reference_images_max > 0) {
      const listed = collectImageRefs();
      const cites = promptCitesImages(base.prompt);
      const images = cites ? listed : [];
      if (cites && !images.length) {
        throw new Error("指令写了 @图片，请在下面放上对应的参考图");
      }
      const cap = activeProfile().edit_reference_images_max;
      if (images.length > cap) throw new Error(`这次最多 ${cap} 张参考图`);
      base.reference_images = images;
      base.source_job_id = sourceJobIdEl.value.trim() || null;
      base.source_video_uri = sourceVideoUriEl.value.trim() || null;
      if (!base.source_video_uri && !base.source_job_id) {
        throw new Error(`${currentMode} 需要提供 gs:// 源视频，或选择一条已完成的任务`);
      }
      setStatus(
        !cites && listed.length
          ? `Submitting ${currentMode}… 指令没有 @图片，参考图不上传。`
          : images.length
            ? `Submitting ${currentMode}… 参考图由服务端下载。`
            : `Submitting ${currentMode}…`
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

document.querySelectorAll("#modeTabs .mode-tab").forEach((btn) => {
  btn.addEventListener("click", () => setMode(btn.dataset.mode));
});
document.querySelectorAll("#genTabs [data-gen]").forEach((btn) => {
  btn.addEventListener("click", () => setGenTask(btn.dataset.gen));
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
  setStatus("已填入当前成片。确认编辑指令后提交 Continue edit。");
});
document.getElementById("useForEdit").addEventListener("click", () => {
  if (selectedJob) applySourceJob(selectedJob);
  setMode("edit");
  setStatus("已填入当前成片。确认编辑指令后提交 Edit video。");
});
document.getElementById("useForExtend").addEventListener("click", () => {
  if (selectedJob) applySourceJob(selectedJob);
  setMode("extend");
  setStatus("已填入当前成片。Duration 是这次续写的秒数，确认后提交 Extend。");
});
document.getElementById("useForUpscale").addEventListener("click", () => {
  if (selectedJob) applySourceJob(selectedJob);
  if (!activeProfile().supports_upscale) {
    modelSelect.value = "gemini-omni-1.2-flash";
  }
  setMode("upscale");
  setStatus("已填入当前成片。放大不接受文字，确认目标分辨率高于源片后提交。");
});
modelSelect.addEventListener("change", () => setMode(currentMode));
customModelEl.addEventListener("change", () => setMode(currentMode));
document.getElementById("addVideo").addEventListener("click", () => addVideoRow());
document.getElementById("uploadVideos").addEventListener("click", () => videoPicker.click());
videoPicker.addEventListener("change", () => {
  const files = [...(videoPicker.files || [])];
  for (const file of files) {
    if (videoListEl.children.length >= 3) {
      setStatus("参考视频最多 3 段");
      break;
    }
    addVideoRow({ file });
  }
  videoPicker.value = "";
});

document.getElementById("addImage").addEventListener("click", () =>
  addImageRow({ removable: true })
);
document.getElementById("uploadImages").addEventListener("click", () => filePicker.click());
filePicker.addEventListener("change", async () => {
  try {
    await handleUploadFiles(filePicker.files);
  } catch (err) {
    setStatus(String(err));
  } finally {
    filePicker.value = "";
  }
});
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
setGenTask("text_to_video");
loadHealth();
ensureExamples().catch((err) => setStatus(String(err)));
refreshJobList().catch(() => {});
