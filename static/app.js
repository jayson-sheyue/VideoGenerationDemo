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
const guideTask = document.getElementById("guideTask");
const guideSteps = document.getElementById("guideSteps");
const guideNote = document.getElementById("guideNote");
const exampleChips = document.getElementById("exampleChips");
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

let pollTimer = null;
let parseTimer = null;
let lastFilledUrlsKey = "";
let currentMode = "generate";
let currentGenTask = "text_to_video";
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
    note: "服务端会把 @图片N 改成 Omni 的 <IMAGE_REF_N>。生成后可「重新生成」复用已上传的 GCS 图。",
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
      "服务端会补上 Keep everything else the same.",
    ],
    note: "源片必须 ≤10s。续写后的长片不能再 Edit。画幅与时长沿用源视频。",
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
    note: "不能改配音或台词。过长视频会报 Editing duration exceeds maximum duration 10。",
    placeholder: "例：Remove the tablet from her hands. Keep everything else the same.",
    promptLabel: "Edit instruction",
    button: "Edit video",
  },
  extend: {
    title: "Extend — 从片尾往后接",
    task: "task=extend",
    steps: [
      "选择源视频。描述接下来几秒发生什么，不要重述原片。",
      "Duration 是本次新增长度（3–10s）。返回的是拼接后的完整成片。",
      "例如 8s 源片再续 8s → 得到约 16s。",
    ],
    note: "源片 1–30s，成片最长约 40s。超过 10s 后不能再 Edit，只能继续 Extend。",
    placeholder: "例：Continue as they watch the interface light up and smile.",
    promptLabel: "What happens next",
    button: "Extend video",
    enhance: "续写指令会略作镜头/节奏润色，不改人物和画风。",
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
  const items =
    currentMode === "generate"
      ? (GEN_EXAMPLES[currentGenTask] || []).map((item, i) => ({
          label: item.label,
          apply: () => applyGenerateExample(currentGenTask, i),
        }))
      : (MODE_EXAMPLES[currentMode] || []).map((text, i) => ({
          label: text.replace(/^Continue as /i, "").slice(0, 18),
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
  guideNote.textContent = g.note;
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
  applyGuide();
}

function setMode(mode) {
  currentMode = mode;
  document.querySelectorAll("#modeTabs .mode-tab").forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.mode === mode);
  });

  const isGenerate = mode === "generate";
  sourceBox.hidden = isGenerate;
  sourceVideoField.hidden = isGenerate;
  audioToggles.hidden = !isGenerate;
  durationField.hidden = mode === "continue" || mode === "edit";
  resolutionField.hidden = mode === "extend";
  aspectField.hidden = !isGenerate;
  enhanceToggle.hidden = !(isGenerate || mode === "extend");

  if (isGenerate) {
    setGenTask(currentGenTask);
  } else {
    genTabs.hidden = true;
    refsSection.hidden = true;
    testGcsBtn.hidden = true;
    applyGuide();
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
const exampleCursor = { continue: 0, edit: 0, extend: 0, generate: 0 };

const GEN_EXAMPLES = {
  text_to_video: [
    {
      label: "庭院灯笼",
      prompt:
        "16:9 cinematic, dusk, illustrated look\nA red paper lantern lifts off a wooden table in a quiet courtyard. Wide shot, 24mm, slow tilt up as it rises and spins. Warm lantern light, cool blue sky. No people.\nNo dialogue. No burned-in subtitles. No background music.",
    },
    {
      label: "竖屏夜市",
      prompt:
        "9:16 handheld documentary\nA street-food stall at night. Steam rises from a wok. The cook flips noodles in one continuous motion. Neon signs bokeh in the background.\nNo spoken lines. Diegetic sizzle only. No burned-in subtitles.",
      aspect_ratio: "9:16",
    },
    {
      label: "微距水滴",
      prompt:
        "16:9 macro, 8 seconds\nA single water droplet hangs from a leaf tip, then falls in slow motion and ripples a dark pond. Shallow depth of field, f/1.8, 100mm.\nSilence except a soft splash. No text on screen.",
    },
  ],
  image_to_video: [
    {
      label: "从静帧推进",
      prompt:
        "Use this image as the first frame.\nThe camera slowly pushes in. A light breeze moves foliage. Keep the original lighting and composition; only add natural motion.\nNo dialogue. No burned-in subtitles. No background music.",
      image_urls: ["https://www.gstatic.com/webp/gallery/1.jpg"],
    },
    {
      label: "天气变化",
      prompt:
        "Start from this exact frame.\nOver 8 seconds the light cools and a light rain begins. Gentle handheld drift. Do not change the subject or crop.\nDiegetic rain only. No speech.",
      image_urls: ["https://www.gstatic.com/webp/gallery/2.jpg"],
    },
  ],
  frames_to_video: [
    {
      label: "两帧运镜",
      prompt:
        "Start on the first image and end on the second image.\nA smooth 8-second camera move interpolates between them: slow dolly plus a slight pan. Keep lighting consistent.\nNo dialogue. No burned-in subtitles.",
      image_urls: [
        "https://www.gstatic.com/webp/gallery/1.jpg",
        "https://www.gstatic.com/webp/gallery/4.jpg",
      ],
    },
    {
      label: "首尾循环",
      prompt:
        "First image is frame 0, second image is the last frame.\nOrbit slowly around the subject so the end pose matches the last still. Motion should feel like one shot, not a cut.\nNo speech. No on-screen text.",
      image_urls: [
        "https://www.gstatic.com/webp/gallery/2.jpg",
        "https://www.gstatic.com/webp/gallery/1.jpg",
      ],
    },
  ],
  reference_to_video: [
    {
      label: "角色锁定分镜",
      prompt: "",
      fetch: true,
    },
    {
      label: "双人仰望",
      prompt:
        "16:9, illustrated look\nCharacter: traveler @图片1\nCompanion: small fox @图片2\nShot 1, 0-8s, medium, 50mm. @图片1 kneels and @图片2 steps closer. They both look up as a lantern rises out of frame.\nThe traveler says: What is it looking for?\nNo burned-in subtitles. No background music.",
      image_urls: [
        "https://www.gstatic.com/webp/gallery/1.jpg",
        "https://www.gstatic.com/webp/gallery/2.jpg",
      ],
    },
  ],
};

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
  setMode("generate");
  setGenTask(kind);
  let item = (GEN_EXAMPLES[kind] || [])[index];
  if (!item) return;
  const chipLabel = item.label;
  if (item.fetch) {
    const res = await fetch("/api/example");
    const data = await res.json();
    item = {
      label: chipLabel,
      prompt: data.prompt,
      image_urls: data.image_urls,
      duration: data.duration,
      resolution: data.resolution,
      aspect_ratio: data.aspect_ratio,
    };
  }
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
  const samples = MODE_EXAMPLES[mode];
  if (!samples) return;
  let text = samples[index % samples.length];
  if (mode !== "extend" && !/keep everything else/i.test(text)) {
    text += " Keep everything else the same.";
  }
  promptEl.value = text;
  if (mode === "extend") document.getElementById("duration").value = "8";
  const maxSeconds = mode === "extend" ? 30 : 10;
  const source = await pickSourceJob({ maxSeconds });
  if (!source) {
    setStatus(
      `示例指令已填入。请先 Generate 一条 ${maxSeconds}s 以内的片子，再选择源视频。`
    );
    return;
  }
  applySourceJob(source);
  setStatus(`示例已填入，源视频 job ${source.id}（${source.duration_sec ?? "?"}s）。`);
}

async function loadGenerateExample() {
  const list = GEN_EXAMPLES[currentGenTask] || [];
  const idx = (exampleCursor.generate || 0) % Math.max(list.length, 1);
  exampleCursor.generate = idx + 1;
  await applyGenerateExample(currentGenTask, idx);
}

async function loadExample() {
  if (currentMode === "generate") {
    await loadGenerateExample();
    return;
  }
  const samples = MODE_EXAMPLES[currentMode];
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
  text_to_video: "文生视频",
  image_to_video: "首帧",
  frames_to_video: "首尾帧",
  reference_to_video: "参考图",
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

async function collectRowImages() {
  const images = [];
  const errors = [];
  const rows = [...imageListEl.children];
  for (let i = 0; i < rows.length; i++) {
    const row = rows[i];
    const stored = rowPayloads.get(row);
    if (stored?.data) {
      images.push({ mime_type: stored.mime_type, data: stored.data });
      continue;
    }
    const url = row.querySelector('input[type="url"]')?.value.trim();
    if (!url) continue;
    setStatus(`正在读取图片 ${i + 1}/${rows.length}…`);
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
      images.push({ mime_type: mime, data: await blobToBase64(blob) });
    } catch (err) {
      errors.push(`图${i + 1}: ${err.message || err}`);
    }
  }
  return { images, errors };
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

  const { images, errors } = await collectRowImages();
  const need = currentGenTask === "frames_to_video" ? 2 : 1;
  if (images.length < need) {
    const detail = errors.length ? `（${errors.join("; ")}）` : "";
    throw new Error(
      currentGenTask === "frames_to_video"
        ? `首尾帧需要两张图，可本地上传或粘贴 URL${detail}`
        : `请上传或粘贴至少一张图${detail}`
    );
  }
  if (errors.length) {
    setStatus(`已读取 ${images.length} 张，其余失败：${errors.join("; ")}`);
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
      generate_task: currentMode === "generate" ? currentGenTask : undefined,
    };

    if (currentMode === "generate") {
      const prepared = await prepareGenerateInput();
      base.prompt = prepared.prompt || prompt;
      base.reference_images = prepared.referenceImages;
      const n = base.reference_images.length;
      setStatus(
        n
          ? `Submitting ${currentGenTask}… (${n} image(s) → GCS → Omni)`
          : `Submitting ${currentGenTask}…`
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
refreshJobList().catch(() => {});
