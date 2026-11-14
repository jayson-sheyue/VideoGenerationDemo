"use strict";
const $ = (id) => document.getElementById(id);
const docBase =
  "https://docs.cloud.google.com/gemini-enterprise-agent-platform/models/video/";
const capabilities = [
  {
    id: "text_to_video",
    label: "text",
    symbol: "T",
    help: "textHelp",
    doc: "generate-videos-from-text",
    mode: "generate",
    images: 0,
  },
  {
    id: "image_to_video",
    label: "first",
    symbol: "▧",
    help: "firstHelp",
    doc: "generate-videos-from-an-image",
    mode: "generate",
    images: 1,
  },
  {
    id: "frames_to_video",
    label: "frames",
    symbol: "▧ ▧",
    help: "framesHelp",
    doc: "generate-videos-from-first-and-last-frames",
    mode: "generate",
    images: 2,
  },
  {
    id: "reference_to_video",
    label: "refs",
    symbol: "◈",
    help: "refsHelp",
    doc: "generate-videos-from-references",
    mode: "generate",
    images: 10,
  },
  {
    id: "edit",
    label: "edit",
    symbol: "✎",
    help: "editHelp",
    doc: "edit-videos",
    mode: "edit",
    images: 2,
  },
  {
    id: "continue",
    label: "continue",
    symbol: "↻",
    help: "continueHelp",
    doc: "edit-videos",
    mode: "continue",
    images: 2,
  },
  {
    id: "extend",
    label: "extend",
    symbol: "↔",
    help: "extendHelp",
    doc: "extend-videos",
    mode: "extend",
    images: 2,
  },
  {
    id: "upscale",
    label: "upscale",
    symbol: "⤢",
    help: "upscaleHelp",
    doc: "upscale-videos",
    mode: "upscale",
    images: 0,
  },
];
const terminal = new Set(["completed", "failed", "interrupted"]);
const upscaleTargets = {
  "360p": ["720p", "1080p"],
  "720p": ["1080p", "4k"],
  "1080p": [],
  "4k": [],
};
let view =
    location.pathname === "/docs"
      ? "reference"
      : ["#library", "#tasks"].includes(location.hash)
        ? location.hash.slice(1)
        : "studio",
  capId = "text_to_video",
  drafts = {},
  jobs = new Map(),
  selectedId = null,
  selectedJob = null,
  selectionEpoch = 0,
  detailTab = "overview",
  workspace = null,
  catalog = [],
  examples = null,
  submitting = false,
  listBusy = false,
  listSignature = "",
  showLimit = 24,
  toastTimer = null,
  booted = false;
let lastAttempt = null,
  comparisonEpoch = 0,
  collectionSignature = "";
const preview = $("preview");
$("previewHost").append(preview);
function node(tag, cls, text) {
  const n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
function button(text, fn, cls = "secondary") {
  const n = node("button", cls, text);
  n.type = "button";
  n.addEventListener("click", fn);
  return n;
}
function fmtTime(value) {
  if (!value) return "—";
  return new Date(value).toLocaleString(
    { en: "en-US", ja: "ja-JP", zh: "zh-CN" }[language],
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" },
  );
}
function titleOf(job) {
  return (
    job.library?.title ||
    (
      job.request?.prompt ||
      job.shot_list ||
      job.meta?.prompt_original ||
      t(kindOf(job))
    )
      .split("\n")[0]
      .slice(0, 90) ||
    job.id
  );
}
function kindOf(job) {
  return (
    capabilities.find(
      (c) =>
        c.id === (job.generate_task || job.meta?.generate_task || job.mode),
    )?.label ||
    job.mode ||
    "text"
  );
}
function currentCap() {
  return capabilities.find((c) => c.id === capId);
}
// How many stills the selected function takes. Upscale takes the clip alone.
function imageLimit(c = currentCap(), p = profile()) {
  if (c.mode === "generate") return c.images;
  if (c.mode === "upscale") return 0;
  return p.edit_reference_images_max || 0;
}
function defaultDraft() {
  return {
    prompt: "",
    duration: 8,
    resolution: "720p",
    aspect: "16:9",
    images: [],
    videos: [],
    sourceId: "",
    sourceUri: "",
    sourceAsset: null,
    seed: "",
    labels: "",
    stateful: true,
    enhance: false,
    audioSpeech: true,
    audioMusic: true,
    audioEffects: true,
    captions: false,
  };
}
function draft() {
  return (drafts[capId] ||= defaultDraft());
}
function modelId() {
  return $("model").value === "custom"
    ? $("customModel").value.trim()
    : $("model").value;
}
function profile() {
  const id = modelId();
  return (
    catalog.find((c) => c.id === id) || {
      id,
      generate_min: 3,
      generate_max: id.includes("1.2") ? 15 : 10,
      frames_max: 10,
      edit_max: id.includes("1.2") ? 15 : 10,
      extend_min: 3,
      extend_max: 10,
      extend_input_max: id.includes("1.2") ? null : 30,
      upscale_max: id.includes("1.2") ? 15 : null,
      reference_images_max: 10,
      edit_reference_images_max: id.includes("1.2") ? 2 : 0,
      reference_videos_max: 3,
      supports_seed: id.includes("1.2"),
      supports_labels: id.includes("1.2"),
      supports_multiturn: id.includes("1.2"),
      supports_upscale: id.includes("1.2"),
    }
  );
}
function toast(text) {
  $("toast").textContent = text;
  $("toast").hidden = false;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => ($("toast").hidden = true), 4500);
}
function error(message) {
  $("formError").textContent = message;
  $("formError").hidden = !message;
}
async function api(url, options = {}) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), options.timeout || 45000);
  try {
    const response = await fetch(url, {
      ...options,
      signal: controller.signal,
      credentials: "same-origin",
    });
    let body;
    try {
      body = await response.json();
    } catch {
      throw new Error(
        response.status === 403
          ? "Access denied. Open this demo with your Cloud Run access."
          : `HTTP ${response.status}`,
      );
    }
    if (!response.ok) {
      const detail = body.detail;
      const message =
        typeof detail === "string"
          ? detail
          : Array.isArray(detail)
            ? detail
                .map((x) => `${x.loc?.slice(-1)[0] || ""}: ${x.msg}`)
                .join("\n")
            : JSON.stringify(body);
      const e = new Error(message);
      e.status = response.status;
      throw e;
    }
    return body;
  } finally {
    clearTimeout(timer);
  }
}
function jsonOptions(method, body) {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
}
function persist() {
  if (!workspace) return;
  try {
    const safe = {};
    let files = false;
    for (const [key, value] of Object.entries(drafts)) {
      safe[key] = { ...value };
      for (const type of ["images", "videos"])
        safe[key][type] = value[type].map((item) => {
          if (item.file && !item.uri) files = true;
          return {
            name: item.name,
            url: item.url,
            uri: item.uri,
            mime_type: item.mime_type,
            missing: Boolean(item.file && !item.uri) || item.missing,
          };
        });
      if (value.sourceAsset) {
        const a = value.sourceAsset;
        if (a.file && !a.uri) files = true;
        safe[key].sourceAsset = {
          name: a.name,
          uri: a.uri,
          mime_type: a.mime_type,
          missing: Boolean(a.file && !a.uri) || a.missing,
        };
      }
    }
    localStorage.setItem(
      "omni-drafts-" + workspace.workspace,
      JSON.stringify({
        drafts: safe,
        capId,
        model: $("model").value,
        custom: $("customModel").value,
      }),
    );
    $("draftStatus").textContent = t(files ? "draftFiles" : "savedDraft");
  } catch {
    $("draftStatus").textContent = t("draftFail");
  }
}
function readForm() {
  const d = draft();
  Object.assign(d, {
    prompt: $("prompt").value,
    duration: Number($("duration").value || 8),
    resolution: $("resolution").value,
    aspect: $("aspect").value,
    sourceId: $("sourceJob").value,
    sourceUri: $("sourceUri").value.trim(),
    seed: $("seed").value,
    labels: $("labels").value,
    stateful: $("stateful").checked,
    enhance: $("enhance").checked,
    audioSpeech: $("audioSpeech").checked,
    audioMusic: $("audioMusic").checked,
    audioEffects: $("audioEffects").checked,
    captions: $("captions").checked,
  });
  return d;
}
function saveDraft() {
  readForm();
  persist();
}
function restoreDrafts() {
  try {
    const raw = JSON.parse(
      localStorage.getItem("omni-drafts-" + workspace.workspace) || "null",
    );
    if (raw) {
      drafts = raw.drafts || {};
      capId = capabilities.some((c) => c.id === raw.capId)
        ? raw.capId
        : "text_to_video";
      $("model").value = raw.model || "gemini-omni-1.2-flash";
      $("customModel").value = raw.custom || "";
    }
    lastAttempt = JSON.parse(
      localStorage.getItem("omni-pending-" + workspace.workspace) || "null",
    );
  } catch {}
  for (const c of capabilities) {
    drafts[c.id] = { ...defaultDraft(), ...(drafts[c.id] || {}) };
  }
}
function setOptions(select, values, wanted) {
  select.replaceChildren(
    ...values.map((value) => {
      const o = node("option", "", String(value));
      o.value = String(value);
      return o;
    }),
  );
  if (values.map(String).includes(String(wanted)))
    select.value = String(wanted);
}
function renderCapabilities() {
  const root = $("capabilities");
  root.replaceChildren();
  for (const c of capabilities) {
    const b = button(
      "",
      () => switchCapability(c.id),
      "capability" + (c.id === capId ? " active" : ""),
    );
    b.setAttribute("aria-pressed", String(c.id === capId));
    b.append(node("span", "symbol", c.symbol), node("span", "", t(c.label)));
    root.append(b);
  }
}
function renderSourceOptions() {
  const old = draft().sourceId;
  const opts = [node("option", "", t("chooseSource"))];
  opts[0].value = "";
  for (const j of jobs.values()) {
    if (j.status !== "completed" || !j.video_uri || j.library?.trashed)
      continue;
    const o = node("option", "", titleOf(j));
    o.value = j.id;
    opts.push(o);
  }
  $("sourceJob").replaceChildren(...opts);
  if (old && !opts.some((o) => o.value === old)) {
    const o = node("option", "", old);
    o.value = old;
    $("sourceJob").append(o);
  }
  $("sourceJob").value = old;
}
function renderForm() {
  const d = draft(),
    c = currentCap(),
    p = profile();
  renderCapabilities();
  $("capabilityTitle").textContent = t(c.label);
  $("capabilityHelp").textContent = t(c.help);
  $("capabilityDoc").href = docBase + c.doc;
  $("prompt").value = d.prompt;
  $("sourceSection").hidden = c.mode === "generate";
  $("promptSection").hidden = c.id === "upscale";
  $("sourceUri").value = d.sourceUri;
  renderSourceOptions();
  $("sourceFileName").textContent = d.sourceAsset?.name || "";
  $("sourceSummary").hidden = !d.sourceId;
  $("sourceSummary").textContent = d.sourceId
    ? titleOf(jobs.get(d.sourceId) || { id: d.sourceId }) +
      " · " +
      t("keepInputs")
    : "";
  const imageCap = imageLimit(c, p);
  $("imageSection").hidden = !imageCap;
  $("videoSection").hidden = c.id !== "reference_to_video";
  $("imageHelp").textContent =
    c.id === "frames_to_video"
      ? t("inputOrder")
      : c.id === "reference_to_video"
        ? t("refsHelp")
        : "";
  renderAttachments();
  $("durationField").hidden = !["generate", "extend"].includes(c.mode);
  $("durationLabel").textContent = t(
    c.mode === "extend" ? "append" : "duration",
  );
  const max =
    c.id === "frames_to_video"
      ? p.frames_max
      : c.mode === "extend"
        ? p.extend_max
        : p.generate_max;
  setOptions(
    $("duration"),
    Array.from({ length: max - 2 }, (_, i) => i + 3),
    d.duration,
  );
  d.duration = Number($("duration").value);
  $("resolutionField").hidden = c.mode === "extend";
  const source = jobs.get(d.sourceId);
  const res =
    c.id === "upscale"
      ? upscaleTargets[
          source?.output_resolution || source?.meta?.output_resolution
        ] || (!source ? ["720p", "1080p", "4k"] : ["720p", "1080p", "4k"])
      : ["360p", "720p", "1080p", "4k"];
  setOptions($("resolution"), res, d.resolution);
  d.resolution = $("resolution").value;
  $("aspectField").hidden = c.mode !== "generate";
  $("aspect").value = d.aspect;
  $("seed").value = d.seed;
  $("labels").value = d.labels;
  $("stateful").checked = d.stateful;
  $("enhance").checked = d.enhance;
  for (const key of ["audioSpeech", "audioMusic", "audioEffects", "captions"])
    $(key).checked = d[key];
  const soundSupported = ["generate", "extend"].includes(c.mode);
  $("audioOptions").hidden = !soundSupported;
  $("audioExtendHelp").hidden = c.mode !== "extend";
  $("seedField").hidden = !p.supports_seed;
  $("labelsField").hidden = !p.supports_labels;
  $("statefulField").hidden =
    !p.supports_multiturn || !["edit", "continue"].includes(c.mode);
  const polish = ["generate", "extend"].includes(c.mode);
  $("enhanceField").hidden = !polish;
  $("enhanceHelp").hidden = !polish;
  $("customModelField").hidden = $("model").value !== "custom";
  $("modelHelp").textContent =
    $("model").value === "custom"
      ? t("modelCustomHelp")
      : `${p.generate_min}–${p.generate_max}s · ${t("edit")} ≤${p.edit_max}s · ${t("rateHint")}`;
  $("modelBadge").textContent = p.label || modelId();
  const unsupported = c.id === "upscale" && !p.supports_upscale;
  $("generate").disabled = submitting || unsupported;
  $("generate").replaceChildren(
    node(
      "span",
      "",
      t(submitting ? "working" : c.mode === "generate" ? "generate" : c.label),
    ),
    node("span", "", "↗"),
  );
  if (unsupported) error(t("notSupported"));
  else error("");
  if (lastAttempt) offerRetry(lastAttempt);
}
// A submission whose reply never arrived: retry the same one, or set it aside.
function offerRetry(attempt) {
  error(t("uncertain"));
  const actions = node("div", "error-actions");
  actions.append(
    button(t("retrySubmit"), () => sendAttempt(attempt), "text-button"),
    button(
      t("setAside"),
      () => {
        lastAttempt = null;
        localStorage.removeItem("omni-pending-" + workspace.workspace);
        renderForm();
      },
      "text-button",
    ),
  );
  $("formError").append(actions);
}
function switchCapability(id) {
  if (booted) readForm();
  capId = id;
  renderForm();
  persist();
}
function releaseMedia(item) {
  if (item?.preview) URL.revokeObjectURL(item.preview);
}
function renderAttachments() {
  const c = currentCap(),
    d = draft();
  for (const kind of ["images", "videos"]) {
    const root = $(kind === "images" ? "imageItems" : "videoItems");
    root.replaceChildren();
    d[kind].forEach((item, index) => {
      const row = node("div", "attachment");
      if (kind === "images") {
        const src = item.preview || item.url;
        if (src && /^(blob:|https?:)/.test(src)) {
          const img = node("img");
          img.src = src;
          img.alt = `${t("images")} ${index + 1}`;
          img.loading = "lazy";
          row.append(img);
        }
      }
      const label = node(
        "span",
        "",
        `${index + 1}. ${item.name || item.url || item.uri || t("fileReattach")}`,
      );
      label.title = label.textContent;
      row.append(label);
      const remove = button(
        "×",
        () => {
          releaseMedia(item);
          d[kind].splice(index, 1);
          renderAttachments();
          persist();
        },
        "icon-button",
      );
      remove.setAttribute("aria-label", `${t("remove")} ${index + 1}`);
      row.append(remove);
      if (item.missing) row.title = t("fileReattach");
      root.append(row);
    });
  }
  $("imageCount").textContent = `${d.images.length}/${imageLimit(c)}`;
}
function addFiles(kind, files) {
  saveDraft();
  const limit =
    kind === "videos" ? profile().reference_videos_max : imageLimit();
  if (draft()[kind].length + files.length > limit) {
    error(t("tooMany"));
    return;
  }
  for (const file of files) {
    if (file.size > 20 * 1024 * 1024) {
      error(t("fileLimit"));
      return;
    }
  }
  for (const file of files)
    draft()[kind].push({
      file,
      name: file.name,
      mime_type: file.type,
      preview: kind === "images" ? URL.createObjectURL(file) : null,
    });
  renderAttachments();
  persist();
}
function showView(next) {
  if (booted) saveDraft();
  if ($("inspector").open) $("inspector").close();
  view = next;
  for (const id of ["studio", "library", "tasks", "reference"])
    $(id + "View").hidden = id !== view;
  document.querySelectorAll("[data-view]").forEach((b) => {
    b.classList.toggle("active", b.dataset.view === view);
    b.setAttribute("aria-current", b.dataset.view === view ? "page" : "false");
  });
  if (next !== "studio") stopPlayback();
  if (next === "library") renderLibrary();
  if (next === "tasks") renderTasks();
  if (next === "reference") renderReference();
  history.replaceState(null, "", next === "reference" ? "/docs" : "/#" + next);
}
function stopPlayback() {
  document.querySelectorAll("video").forEach((v) => v.pause());
}
function showInspector() {
  if (view === "studio") return;
  $("closeInspector").hidden = false;
  $("inspector").append(preview);
  if (!$("inspector").open) $("inspector").showModal();
}
$("inspector").addEventListener("close", () => {
  stopPlayback();
  $("previewHost").append(preview);
  $("closeInspector").hidden = true;
});
function emptyState(title, help, cta = false) {
  const n = node("div", "empty-state");
  n.append(
    node("span", "symbol", "✦"),
    node("h2", "", t(title)),
    node("p", "", t(help)),
  );
  if (cta) n.append(button(t("newWork"), () => showView("studio"), "primary"));
  return n;
}
function searchable(job) {
  return [
    job.id,
    titleOf(job),
    job.shot_list,
    job.library?.collection,
    job.library?.notes,
    job.model,
    job.mode,
  ]
    .join(" ")
    .toLowerCase();
}
function renderCollections() {
  const values = [
    ...new Set(
      [...jobs.values()].map((j) => j.library?.collection).filter(Boolean),
    ),
  ].sort();
  const signature = JSON.stringify(values) + language;
  if (signature === collectionSignature) return;
  collectionSignature = signature;
  const selected = $("collectionFilter").value;
  $("collectionFilter").replaceChildren(
    node("option", "", t("allCollections")),
  );
  $("collectionFilter").firstChild.value = "";
  $("collectionNames").replaceChildren();
  for (const v of values) {
    const o = node("option", "", v);
    o.value = v;
    $("collectionFilter").append(o);
    $("collectionNames").append(o.cloneNode(true));
  }
  $("collectionFilter").value = values.includes(selected) ? selected : "";
}
function renderLibrary() {
  renderCollections();
  const root = $("libraryGrid"),
    search = $("librarySearch").value.toLowerCase(),
    filter = $("libraryFilter").value,
    group = $("collectionFilter").value;
  const all = [...jobs.values()].filter(
    (j) => j.status === "completed" && j.video_url,
  );
  const rows = all.filter((j) => {
    const l = j.library || {};
    if (filter === "trash" ? !l.trashed : l.trashed) return false;
    if (filter === "archived" ? !l.archived : filter !== "trash" && l.archived)
      return false;
    if (filter === "favorites" && !l.favorite) return false;
    return (!group || l.collection === group) && searchable(j).includes(search);
  });
  root.replaceChildren();
  if (!rows.length) {
    root.append(
      emptyState(
        all.length ? "noMatches" : "noWorks",
        all.length ? "noMatchesHelp" : "noWorksHelp",
        !all.length,
      ),
    );
    $("moreWorks").hidden = true;
    return;
  }
  for (const job of rows.slice(0, showLimit)) {
    const card = node("article", "work-card");
    const open = button("", () => selectJob(job.id, true), "card-open");
    const media = node("div", "card-media");
    const v = node("video");
    v.muted = true;
    v.playsInline = true;
    v.preload = "none";
    v.dataset.src = job.video_url + "#t=0.1";
    v.setAttribute("aria-hidden", "true");
    media.append(v, node("span", "play-mark", "▶"));
    const copy = node("div", "card-copy");
    copy.append(
      node("h3", "", titleOf(job)),
      node(
        "p",
        "",
        `${t(kindOf(job))} · ${job.duration_sec ? job.duration_sec.toFixed(1) + "s" : "—"} · ${fmtTime(job.created_at)}`,
      ),
    );
    open.append(media, copy);
    card.append(open);
    const favorite = button(
      job.library?.favorite ? "★" : "☆",
      () =>
        updateLibrary(job, { favorite: !job.library?.favorite }).catch((e) =>
          toast(e.message),
        ),
      "card-favorite",
    );
    favorite.setAttribute("aria-label", t("favorite"));
    favorite.setAttribute(
      "aria-pressed",
      String(Boolean(job.library?.favorite)),
    );
    card.append(favorite);
    root.append(card);
    thumbObserver.observe(v);
  }
  $("moreWorks").hidden = rows.length <= showLimit;
}
const thumbObserver = new IntersectionObserver(
  (entries) => {
    for (const e of entries)
      if (e.isIntersecting) {
        const video = e.target;
        if (video.dataset.src) {
          video.src = video.dataset.src;
          video.preload = "metadata";
          delete video.dataset.src;
        }
        thumbObserver.unobserve(video);
      }
  },
  { rootMargin: "100px" },
);
function renderTasks() {
  const root = $("taskList"),
    search = $("taskSearch").value.toLowerCase(),
    filter = $("taskFilter").value;
  const all = [...jobs.values()];
  const rows = all.filter(
    (j) =>
      !j.library?.trashed &&
      searchable(j).includes(search) &&
      (filter === "all" ||
        (filter === "active" && !terminal.has(j.status)) ||
        (filter === "completed" && j.status === "completed") ||
        (filter === "failed" && ["failed", "interrupted"].includes(j.status))),
  );
  root.replaceChildren();
  if (!rows.length) {
    root.append(
      emptyState(
        all.length ? "noMatches" : "noTasks",
        all.length ? "noMatchesHelp" : "noTasksHelp",
      ),
    );
    return;
  }
  for (const j of rows) {
    const row = button("", () => selectJob(j.id, true), "task-row");
    const copy = node("span", "task-copy");
    copy.append(
      node("strong", "", titleOf(j)),
      node(
        "small",
        "",
        `${t(kindOf(j))} · ${j.model || j.meta?.model || "Omni"} · ${j.id.slice(0, 8)}`,
      ),
    );
    row.append(
      node(
        "span",
        "task-icon",
        capabilities.find((c) => c.label === kindOf(j))?.symbol || "◈",
      ),
      copy,
      node("span", "task-time", fmtTime(j.created_at)),
      node("span", "status-pill " + j.status, t(j.status)),
      node("span", "", "↗"),
    );
    root.append(row);
  }
}
function renderRecent() {
  const root = $("recentItems");
  root.replaceChildren();
  for (const j of [...jobs.values()]
    .filter((j) => !j.library?.trashed)
    .slice(0, 4)) {
    const row = button("", () => selectJob(j.id, false), "recent-row");
    const copy = node("span", "recent-text");
    copy.append(
      node("strong", "", titleOf(j)),
      node("small", "", `${t(kindOf(j))} · ${fmtTime(j.created_at)}`),
    );
    row.append(
      node(
        "span",
        "recent-icon",
        capabilities.find((c) => c.label === kindOf(j))?.symbol || "◈",
      ),
      copy,
      node("span", "status-pill " + j.status, t(j.status)),
    );
    root.append(row);
  }
  if (!root.children.length) root.append(node("p", "subtle", t("noTasksHelp")));
}
function clearStage() {
  const v = $("player");
  v.pause();
  v.removeAttribute("src");
  v.load();
  v.hidden = true;
  $("playbackError").hidden = true;
}
function rememberSelection() {
  if (!workspace) return;
  try {
    if (selectedId)
      localStorage.setItem("omni-selected-" + workspace.workspace, selectedId);
  } catch {}
}
async function selectJob(id, modal = false) {
  const epoch = ++selectionEpoch;
  selectedId = id;
  rememberSelection();
  selectedJob = null;
  detailTab = "overview";
  comparisonEpoch++;
  clearStage();
  $("workDetails").hidden = true;
  $("stageEmpty").hidden = true;
  $("stageState").hidden = false;
  $("stageState").textContent = t("loading");
  $("previewTitle").textContent = jobs.has(id)
    ? titleOf(jobs.get(id))
    : t("loading");
  if (modal) showInspector();
  try {
    const job = await api("/api/jobs/" + id);
    if (epoch !== selectionEpoch) return;
    selectedJob = job;
    jobs.set(id, { ...jobs.get(id), ...job });
    renderPreview(job);
  } catch (e) {
    if (epoch === selectionEpoch) {
      $("stageState").textContent = e.message;
      toast(t("inspectError"));
    }
  }
}
function renderPreview(job) {
  if (job.id !== selectedId) return;
  selectedJob = job;
  $("previewTitle").textContent = titleOf(job);
  $("stageEmpty").hidden = true;
  $("workDetails").hidden = false;
  $("stageState").hidden = job.status === "completed" && Boolean(job.video_url);
  const v = $("player");
  if (!$("stageState").hidden) {
    clearStage();
    $("stageState").replaceChildren();
    if (!terminal.has(job.status))
      $("stageState").append(node("span", "spinner"));
    $("stageState").append(
      node("strong", "", t(job.status)),
      node(
        "p",
        "",
        terminal.has(job.status) ? job.error || job.message : t("noCancel"),
      ),
    );
  } else {
    v.hidden = false;
    if (v.getAttribute("src") !== job.video_url) {
      v.pause();
      v.src = job.video_url;
      v.load();
    }
  }
  $("workStatus").textContent = t(job.status);
  $("workStatus").className = "status-pill " + job.status;
  $("workSpecs").textContent = [
    job.duration_sec ? `${job.duration_sec.toFixed(1)}s` : null,
    job.output_resolution || job.meta?.output_resolution,
    fmtTime(job.created_at),
  ]
    .filter(Boolean)
    .join(" · ");
  $("favorite").textContent = job.library?.favorite ? "★" : "☆";
  $("favorite").setAttribute(
    "aria-pressed",
    String(Boolean(job.library?.favorite)),
  );
  $("downloadVideo").hidden = job.status !== "completed" || !job.video_url;
  $("downloadVideo").href = `/api/jobs/${job.id}/download`;
  $("downloadVideo").download = `omni-${job.id}.mp4`;
  $("exportJob").href = `/api/jobs/${job.id}/export`;
  const actions = $("nextActions");
  actions.replaceChildren();
  if (job.status === "completed" && job.video_uri) {
    for (const id of ["continue", "edit", "extend", "upscale"]) {
      const c = capabilities.find((c) => c.id === id);
      const b = button(t(c.label), () => useWork(job, id), "");
      const p =
        catalog.find((p) => p.id === (job.model || job.meta?.model)) ||
        profile();
      let reason = "";
      const limit =
        id === "upscale"
          ? p.upscale_max
          : id === "extend"
            ? p.extend_input_max
            : p.edit_max;
      if (id === "upscale" && !p.supports_upscale) reason = t("notSupported");
      else if (limit != null && job.duration_sec > limit + 0.05)
        reason = t("sourceTooLong");
      else if (
        id === "upscale" &&
        upscaleTargets[job.output_resolution || job.meta?.output_resolution]
          ?.length === 0
      )
        reason = t("noUpscale");
      b.disabled = Boolean(reason);
      b.title = reason;
      actions.append(b);
    }
  }
  if (job.request && Object.keys(job.request).length)
    actions.append(
      button(t("restoreSettings"), () => restoreSettings(job), ""),
    );
  if (job.status === "interrupted" && job.interaction_id)
    actions.append(button(t("recover"), () => recoverJob(job), ""));
  $("trashWork").disabled = !terminal.has(job.status);
  renderDetail(job);
}
// The sound choices a task was submitted with, when it recorded any.
function soundOf(job) {
  return job.request?.audio_preferences || job.meta?.audio_preferences || {};
}
// The parameters this task was submitted with, limited to what its function takes.
function settingsOf(job) {
  const r = job.request || {};
  const mode = r.mode || job.mode;
  const parts = [];
  if (["generate", "extend"].includes(mode) && r.duration)
    parts.push((mode === "extend" ? "+" : "") + r.duration + "s");
  if (mode !== "extend" && (r.resolution || job.output_resolution))
    parts.push(r.resolution || job.output_resolution);
  if (mode === "generate" && r.aspect_ratio) parts.push(r.aspect_ratio);
  if (["generate", "extend"].includes(mode)) {
    const audio = r.audio_preferences || job.meta?.audio_preferences;
    if (audio) {
      for (const [key, label] of [["speech", "audioSpeech"], ["music", "audioMusic"], ["effects", "audioEffects"]])
        parts.push(`${t(label)}: ${t(audio[key] ? "audioOn" : "audioOff")}`);
      parts.push(`${t("captions")}: ${t(r.no_subtitles === false ? "audioOn" : "audioOff")}`);
    }
  }
  if (r.seed != null) parts.push("seed " + r.seed);
  for (const [k, v] of Object.entries(r.labels || {})) parts.push(`${k}=${v}`);
  return parts.join(" · ") || "—";
}
function detailHeading(root, key, text) {
  root.append(node("h3", "", t(key)), node("pre", "", text || "—"));
}
function renderDetail(job) {
  document
    .querySelectorAll("[data-detail]")
    .forEach((b) =>
      b.classList.toggle("active", b.dataset.detail === detailTab),
    );
  const root = $("detailContent");
  root.replaceChildren();
  if (detailTab === "overview") {
    const dl = node("dl");
    for (const [k, v] of [
      ["model", job.model || job.meta?.model],
      ["created", fmtTime(job.created_at)],
      [
        "source",
        (jobs.has(job.source_job_id) && titleOf(jobs.get(job.source_job_id))) ||
          job.source_video_uri ||
          job.source_job_id ||
          "—",
      ],
      ["settings", settingsOf(job)],
      ["collection", job.library?.collection || "—"],
    ])
      dl.append(node("dt", "", t(k)), node("dd", "", v));
    root.append(dl);
    if (job.library?.notes) detailHeading(root, "notes", job.library.notes);
    detailHeading(
      root,
      "originalPrompt",
      job.request?.prompt || job.meta?.prompt_original || job.shot_list,
    );
    if (job.error) detailHeading(root, "needsAttention", job.error);
  } else if (detailTab === "inputs") {
    detailHeading(
      root,
      "originalPrompt",
      job.request?.prompt || job.meta?.prompt_original || job.shot_list,
    );
    const images = job.meta?.gcs_ref_items || [],
      videos = job.meta?.gcs_video_items || [];
    const source = jobs.get(job.source_job_id);
    if (source) {
      root.append(node("h3", "", t("sourceWork")));
      const open = button(
        "",
        () => selectJob(source.id, $("inspector").open),
        "version-row",
      );
      open.append(
        node("span", "", titleOf(source)),
        node(
          "small",
          "",
          source.duration_sec ? `${source.duration_sec.toFixed(1)}s` : "",
        ),
        node("span", "", "↗"),
      );
      root.append(open);
    } else if (job.source_video_uri)
      detailHeading(root, "sourceWork", job.source_video_uri);
    if (!images.length && !videos.length && !source && !job.source_video_uri)
      root.append(node("p", "subtle", t("noInputs")));
    const grid = node("div", "input-grid");
    for (const [kind, items] of [
      ["images", images],
      ["videos", videos],
    ])
      items.forEach((item, i) => {
        const figure = node("figure", "input-figure");
        const src = `/api/jobs/${job.id}/assets/${kind}/${i}`;
        const media = node(kind === "images" ? "img" : "video");
        media.src = src;
        const label = `${t(kind === "images" ? "images" : "videoRefs")} ${i + 1}`;
        if (kind === "images") {
          media.alt = label;
          media.loading = "lazy";
          const link = node("a");
          link.href = src;
          link.target = "_blank";
          link.rel = "noopener";
          link.append(media);
          figure.append(link);
        } else {
          media.controls = true;
          media.preload = "metadata";
          figure.append(media);
        }
        figure.append(node("figcaption", "subtle", label));
        grid.append(figure);
      });
    if (grid.children.length) root.append(grid);
    detailHeading(root, "sentPrompt", job.meta?.final_prompt_preview);
  } else if (detailTab === "technical") {
    detailHeading(root, "sentPrompt", job.meta?.final_prompt_preview);
    detailHeading(root, "technical", JSON.stringify(job, null, 2));
    const list = node("ol", "timeline");
    for (const event of job.events || []) {
      const li = node("li");
      li.append(
        node("time", "", fmtTime(event.at)),
        node("span", "", `${t(event.status)} · ${event.message}`),
      );
      list.append(li);
    }
    root.append(list);
  } else renderVersions(job, root);
}
async function renderVersions(job, root) {
  const epoch = ++comparisonEpoch;
  root.textContent = t("versionsLoading");
  try {
    const data = await api(`/api/jobs/${job.id}/lineage`);
    if (
      epoch !== comparisonEpoch ||
      selectedId !== job.id ||
      detailTab !== "versions"
    )
      return;
    root.replaceChildren();
    if (data.clips.length < 2)
      root.append(node("p", "subtle", t("noVersions")));
    for (const clip of data.clips) {
      const row = button(
        "",
        () => selectJob(clip.job_id, $("inspector").open),
        "version-row" + (clip.job_id === job.id ? " active" : ""),
      );
      row.append(
        node(
          "span",
          "",
          `${t(capabilities.find((c) => c.id === (clip.generate_task || clip.mode))?.label || clip.mode)} · ${fmtTime(clip.created_at)}`,
        ),
        node("small", "", `${clip.duration_sec || "—"}s`),
        node("span", "", "↗"),
      );
      root.append(row);
    }
    if (data.clips.length >= 2) {
      const pair = node("div", "parameter-grid");
      pair.style.gridTemplateColumns = "1fr 1fr";
      const prev =
        data.clips.find((c) => c.job_id === job.source_job_id) || data.clips[0];
      const curr =
        data.clips.find((c) => c.job_id === job.id) || data.clips.at(-1);
      for (const c of [prev, curr]) {
        const vid = node("video");
        vid.controls = true;
        vid.muted = true;
        vid.playsInline = true;
        vid.preload = "metadata";
        vid.src = c.video_url;
        vid.style.width = "100%";
        vid.style.maxHeight = "180px";
        pair.append(vid);
      }
      const controls = node("div", "work-tools");
      root.append(pair, controls);
      controls.append(
        button(
          t("playCompare"),
          () => {
            $("player").pause();
            pair.querySelectorAll("video").forEach((v) => {
              v.currentTime = 0;
              v.play().catch(() => toast(t("playbackError")));
            });
          },
          "text-button",
        ),
        button(
          t("stopCompare"),
          () => pair.querySelectorAll("video").forEach((v) => v.pause()),
          "text-button",
        ),
      );
    }
  } catch (e) {
    if (epoch === comparisonEpoch) root.textContent = e.message;
  }
}
function useWork(job, id) {
  saveDraft();
  const chosen = job.model || job.meta?.model;
  if (chosen) {
    if ([...$("model").options].some((o) => o.value === chosen))
      $("model").value = chosen;
    else {
      $("model").value = "custom";
      $("customModel").value = chosen;
    }
  }
  capId = id;
  const d = draft();
  d.sourceId = job.id;
  d.sourceUri = "";
  d.sourceAsset = null;
  if (id === "upscale")
    d.resolution =
      (upscaleTargets[job.output_resolution || job.meta?.output_resolution] || [
        "1080p",
      ])[0] || "";
  if ($("inspector").open) $("inspector").close();
  view = "studio";
  renderForm();
  showView("studio");
  persist();
  toast(t("sourceSelected"));
  $("sourceSection").scrollIntoView({ block: "nearest", behavior: "smooth" });
}
function restoreSettings(job) {
  saveDraft();
  const req = job.request || {};
  capId =
    req.mode === "generate"
      ? req.generate_task || "text_to_video"
      : req.mode || job.mode;
  if (!capabilities.some((c) => c.id === capId)) capId = "text_to_video";
  const d = {
    ...defaultDraft(),
    prompt: req.prompt || job.meta?.prompt_original || "",
    duration: req.duration || 8,
    resolution: req.resolution || "720p",
    aspect: req.aspect_ratio || "16:9",
    seed: req.seed ?? "",
    labels: Object.entries(req.labels || {})
      .map(([k, v]) => `${k}=${v}`)
      .join(", "),
    sourceId: job.source_job_id || "",
    sourceUri: req.source_video_uri || "",
    stateful: req.stateful !== false,
    enhance: req.enhance_prompt || false,
    // Tasks from before the sound choices existed open with the standard choices.
    audioSpeech: soundOf(job).speech !== false,
    audioMusic: soundOf(job).music !== false,
    audioEffects: soundOf(job).effects !== false,
    captions: (req.no_subtitles ?? job.meta?.no_subtitles) === false,
    images: (job.meta?.gcs_ref_items || []).map((x) => ({
      ...x,
      name: x.uri.split("/").at(-1),
    })),
    videos: (job.meta?.gcs_video_items || []).map((x) => ({
      ...x,
      name: x.uri.split("/").at(-1),
    })),
  };
  if (!d.images.length)
    d.images = (req.reference_images || [])
      .filter((x) => x.url || x.uri)
      .map((x) => ({ ...x, name: x.url || x.uri }));
  if (!d.videos.length)
    d.videos = (req.reference_videos || []).filter((x) => x.uri);
  drafts[capId] = d;
  const model = req.model || job.model || "gemini-omni-1.2-flash";
  if ([...$("model").options].some((o) => o.value === model))
    $("model").value = model;
  else {
    $("model").value = "custom";
    $("customModel").value = model;
  }
  if ($("inspector").open) $("inspector").close();
  renderForm();
  showView("studio");
  persist();
  toast(t("copiedSettings"));
}
// Changes to one work are sent one after another, each on top of the latest
// saved version, so quick consecutive clicks all land.
const librarySaves = new Map();
function updateLibrary(job, changes) {
  const save = (librarySaves.get(job.id) || Promise.resolve())
    .catch(() => {})
    .then(() => saveLibrary(job, changes));
  librarySaves.set(job.id, save);
  return save;
}
async function saveLibrary(job, changes) {
  const known = [jobs.get(job.id), selectedJob?.id === job.id && selectedJob, job]
    .filter(Boolean)
    .map((j) => j.library?.version || 0);
  const updated = await api(
    "/api/jobs/" + job.id,
    jsonOptions("PATCH", { version: Math.max(...known), ...changes }),
  );
  jobs.set(job.id, updated);
  if (selectedId === job.id) renderPreview(updated);
  renderRecent();
  if (view === "library") renderLibrary();
  if (view === "tasks") renderTasks();
  toast(t("saved"));
  return updated;
}
async function recoverJob(job) {
  try {
    await api(`/api/jobs/${job.id}/recover`, { method: "POST" });
    await selectJob(job.id, $("inspector").open);
    await refreshJobs();
  } catch (e) {
    toast(e.message);
  }
}
function openManage() {
  if (!selectedJob) return;
  const l = selectedJob.library || {};
  $("workTitle").value = l.title || titleOf(selectedJob);
  $("workCollection").value = l.collection || "";
  $("workNotes").value = l.notes || "";
  $("archiveWork").textContent = t(l.archived ? "unarchive" : "archive");
  $("trashWork").textContent = t(l.trashed ? "restore" : "moveTrash");
  $("manageError").hidden = true;
  $("manageDialog").showModal();
}
async function manage(changes) {
  if (!selectedJob) return;
  const controls = [...$("manageForm").querySelectorAll("button")];
  for (const c of controls) c.disabled = true;
  try {
    await updateLibrary(selectedJob, changes);
    $("manageDialog").close();
  } catch (e) {
    $("manageError").textContent = e.message;
    $("manageError").hidden = false;
    if (e.status === 409) {
      const fresh = await api("/api/jobs/" + selectedJob.id).catch(() => null);
      if (fresh) {
        selectedJob = fresh;
        jobs.set(fresh.id, fresh);
      }
    }
  } finally {
    for (const c of controls) c.disabled = false;
    $("trashWork").disabled = !terminal.has(selectedJob.status);
  }
}
async function refreshJobs() {
  if (listBusy || !workspace) return;
  listBusy = true;
  try {
    const data = await api("/api/jobs");
    $("connectionBanner").hidden = data.storage_ok !== false;
    $("connectionBanner").textContent = t("storageWarning");
    const next = new Map();
    for (const row of data.jobs || []) {
      const old = jobs.get(row.id);
      next.set(row.id, row);
      if (old && !terminal.has(old.status) && terminal.has(row.status))
        toast(
          `${t(row.status === "completed" ? "finished" : "taskFailed")} · ${titleOf(row)}`,
        );
    }
    jobs = next;
    const signature = JSON.stringify(data.jobs);
    const changed = signature !== listSignature;
    listSignature = signature;
    const n = [...jobs.values()].filter((j) => !terminal.has(j.status)).length;
    $("activeCount").textContent = n;
    $("activeCount").hidden = !n;
    if (changed) {
      renderRecent();
      renderSourceOptions();
      renderCollections();
      if (view === "library") renderLibrary();
      if (view === "tasks") renderTasks();
      const summary = jobs.get(selectedId);
      if (
        selectedJob &&
        summary &&
        (summary.updated_at !== selectedJob.updated_at ||
          JSON.stringify(summary.library) !==
            JSON.stringify(selectedJob.library))
      ) {
        const id = selectedId,
          epoch = selectionEpoch;
        const detail = await api("/api/jobs/" + id);
        if (epoch === selectionEpoch && id === selectedId)
          renderPreview(detail);
      }
    }
  } catch (e) {
    $("connectionBanner").textContent = e.status ? e.message : t("offline");
    $("connectionBanner").hidden = false;
  } finally {
    listBusy = false;
  }
}
async function upload(item) {
  if (item.missing) throw new Error(t("fileReattach"));
  if (item.uri) return { uri: item.uri, mime_type: item.mime_type };
  if (item.url)
    return { url: item.url, mime_type: item.mime_type || "image/jpeg" };
  if (!item.file) throw new Error(t("needImages"));
  const data = new FormData();
  data.append("file", item.file);
  const result = await api("/api/assets", {
    method: "POST",
    body: data,
    timeout: 120000,
  });
  Object.assign(item, result);
  return { uri: result.uri, mime_type: result.mime_type };
}
function parseLabels(value) {
  const result = {};
  for (const pair of value
    .split(",")
    .map((x) => x.trim())
    .filter(Boolean)) {
    const i = pair.indexOf("=");
    if (i < 1) throw new Error(t("invalidLabels"));
    const k = pair.slice(0, i).trim(),
      v = pair.slice(i + 1).trim();
    if (!/^[a-z][a-z0-9_-]{0,62}$/.test(k) || !/^[a-z0-9_-]{0,63}$/.test(v))
      throw new Error(t("invalidLabels"));
    result[k] = v;
  }
  return result;
}
async function submit() {
  if (submitting) return;
  error("");
  saveDraft();
  const c = { ...currentCap() },
    d = {
      ...draft(),
      images: [...draft().images],
      videos: [...draft().videos],
    },
    p = { ...profile() },
    model = modelId();
  try {
    if (!model) throw new Error("Enter a model ID.");
    if (c.id !== "upscale" && !d.prompt.trim())
      throw new Error(t("needPrompt"));
    if (c.id === "upscale" && !p.supports_upscale)
      throw new Error(t("notSupported"));
    if (c.mode !== "generate" && !d.sourceId && !d.sourceUri && !d.sourceAsset)
      throw new Error(t("needSource"));
    if (d.sourceUri && !d.sourceUri.startsWith("gs://"))
      throw new Error(t("needSource"));
    const imageCount = imageLimit(c, p);
    if (d.images.length > imageCount && imageCount > 0)
      throw new Error(t("tooMany"));
    if (
      (c.images === 1 && d.images.length !== 1) ||
      (c.id === "frames_to_video" && d.images.length !== 2) ||
      (c.id === "reference_to_video" && !d.images.length && !d.videos.length)
    )
      throw new Error(t("needImages"));
    const source = jobs.get(d.sourceId);
    const limit = ["edit", "continue"].includes(c.mode)
      ? p.edit_max
      : c.mode === "upscale"
        ? p.upscale_max
        : c.mode === "extend"
          ? p.extend_input_max
          : null;
    if (
      !d.sourceUri &&
      !d.sourceAsset &&
      source &&
      limit != null &&
      source.duration_sec > limit + 0.05
    )
      throw new Error(t("sourceTooLong"));
    if (c.id === "upscale" && !d.resolution) throw new Error(t("noUpscale"));
    const seed = d.seed === "" ? null : Number(d.seed);
    if (
      p.supports_seed &&
      seed !== null &&
      (!Number.isInteger(seed) || seed < 0 || seed > 4294967295)
    )
      throw new Error(t("invalidSeed"));
    const labels = p.supports_labels ? parseLabels(d.labels) : {};
    submitting = true;
    $("generate").disabled = true;
    $("generate").textContent = t("uploading");
    const body = {
      prompt: c.id === "upscale" ? "" : d.prompt.trim(),
      mode: c.mode,
      generate_task: c.mode === "generate" ? c.id : null,
      duration: d.duration || 8,
      resolution: d.resolution || "720p",
      aspect_ratio: d.aspect,
      model,
      seed: p.supports_seed ? seed : null,
      labels,
      stateful: p.supports_multiturn && d.stateful,
      enhance_prompt: ["generate", "extend"].includes(c.mode) && d.enhance,
      audio_preferences: ["generate", "extend"].includes(c.mode)
        ? { speech: d.audioSpeech, music: d.audioMusic, effects: d.audioEffects }
        : null,
      no_subtitles: !d.captions,
      reference_images: [],
      reference_videos: [],
      source_job_id: c.mode === "generate" ? null : d.sourceId || null,
      source_video_uri: c.mode === "generate" ? null : d.sourceUri || null,
    };
    if (imageCount > 0) {
      for (const item of d.images)
        body.reference_images.push(await upload(item));
    }
    if (c.id === "reference_to_video") {
      for (const item of d.videos)
        body.reference_videos.push(await upload(item));
    }
    if (c.mode !== "generate" && d.sourceAsset) {
      const result = await upload(d.sourceAsset);
      body.source_video_uri = result.uri;
      body.source_job_id = null;
    }
    persist();
    const signature = JSON.stringify(body);
    if (!lastAttempt || lastAttempt.signature !== signature) {
      lastAttempt = {
        signature,
        body: { ...body, request_id: crypto.randomUUID() },
      };
      localStorage.setItem(
        "omni-pending-" + workspace.workspace,
        JSON.stringify(lastAttempt),
      );
    }
    await sendAttempt(lastAttempt);
  } catch (e) {
    error(e.message);
  } finally {
    submitting = false;
    $("generate").disabled = false;
    $("generate").replaceChildren(
      node(
        "span",
        "",
        t(currentCap().mode === "generate" ? "generate" : currentCap().label),
      ),
      node("span", "", "↗"),
    );
  }
}
async function sendAttempt(attempt) {
  const epoch = selectionEpoch,
    currentView = view;
  submitting = true;
  $("generate").disabled = true;
  try {
    const job = await api("/api/generate", jsonOptions("POST", attempt.body));
    lastAttempt = null;
    localStorage.removeItem("omni-pending-" + workspace.workspace);
    error("");
    jobs.set(job.id, job);
    toast(t("submitted"));
    await refreshJobs();
    if (epoch === selectionEpoch && currentView === view && view === "studio") {
      selectedId = job.id;
      rememberSelection();
      selectionEpoch++;
      selectedJob = job;
      detailTab = "overview";
      renderPreview(job);
    }
  } catch (e) {
    if (e.status && e.status < 500) {
      lastAttempt = null;
      localStorage.removeItem("omni-pending-" + workspace.workspace);
      error(e.message);
    } else offerRetry(attempt);
  } finally {
    submitting = false;
    $("generate").disabled = false;
  }
}
// A page closed mid-submit comes back with the attempt still on file. If the
// task did start, there is nothing left to decide.
async function settlePending() {
  if (!lastAttempt) return;
  try {
    const job = await api(
      "/api/submissions/" + lastAttempt.body.request_id,
    );
    lastAttempt = null;
    localStorage.removeItem("omni-pending-" + workspace.workspace);
    jobs.set(job.id, { ...jobs.get(job.id), ...job });
    renderForm();
    renderRecent();
  } catch {}
}
async function loadExample() {
  try {
    examples ||= await api("/api/examples");
    const c = currentCap(),
      d = readForm();
    if (c.mode === "generate") {
      const options = examples.generate[c.id] || [];
      const example = options[(d.exampleIndex || 0) % options.length];
      if (!example) return;
      d.exampleIndex = (d.exampleIndex || 0) + 1;
      d.prompt = example.prompt;
      d.duration = example.duration || 8;
      d.aspect = example.aspect_ratio || "16:9";
      d.images.forEach(releaseMedia);
      d.images = (example.image_urls || []).map((url) => ({
        url,
        name: url.split("/").at(-1),
        mime_type: "image/jpeg",
      }));
    } else {
      const options = examples[c.mode] || [];
      d.prompt = options[(d.exampleIndex || 0) % options.length] || "";
      d.exampleIndex = (d.exampleIndex || 0) + 1;
    }
    renderForm();
    persist();
  } catch (e) {
    error(e.message);
  }
}
function renderReference() {
  const root = $("referenceGrid");
  root.replaceChildren();
  for (const c of capabilities) {
    const card = node("article", "reference-card");
    card.append(
      node("span", "symbol", c.symbol),
      node("h2", "", t(c.label)),
      node("p", "", t(c.help)),
    );
    const a = node("a", "", t("officialGuide"));
    a.href = docBase + c.doc;
    a.target = "_blank";
    a.rel = "noopener";
    card.append(
      a,
      button(
        t("tryCapability"),
        () => {
          switchCapability(c.id);
          showView("studio");
        },
        "secondary",
      ),
    );
    root.append(card);
  }
  renderLimits();
}
// The numbers the studio enforces, straight from the model profiles.
function renderLimits() {
  const table = $("limits");
  table.replaceChildren();
  if (!catalog.length) return;
  const sec = (n) => `${n} s`;
  const yes = (v) => (v ? "✓" : "—");
  const rows = [
    ["limitGenerate", (p) => `${p.generate_min}–${p.generate_max} s`],
    ["limitFrames", (p) => `${p.generate_min}–${p.frames_max} s`],
    ["limitEdit", (p) => "≤ " + sec(p.edit_max)],
    ["limitExtendAdd", (p) => `${p.extend_min}–${p.extend_max} s`],
    ["limitUpscale", (p) => (p.upscale_max == null ? "—" : "≤ " + sec(p.upscale_max))],
    ["limitRefImages", (p) => `≤ ${p.reference_images_max}`],
    ["limitRefClips", (p) => `≤ ${p.reference_videos_max}`],
    [
      "limitEditImages",
      (p) =>
        p.edit_reference_images_max ? `≤ ${p.edit_reference_images_max}` : "—",
    ],
    ["limitMultiturn", (p) => yes(p.supports_multiturn)],
    ["limitSeed", (p) => yes(p.supports_seed)],
    ["limitLabels", (p) => yes(p.supports_labels)],
    ["resolution", (p) => p.resolutions.join(" · ")],
    ["aspect", (p) => p.aspect_ratios.join(" · ")],
  ];
  const head = node("tr");
  head.append(node("th", "", ""));
  for (const p of catalog) {
    const th = node("th", "", `${p.label} · ${p.stage}`);
    th.append(node("code", "", p.id));
    head.append(th);
  }
  const thead = node("thead"),
    tbody = node("tbody");
  thead.append(head);
  for (const [key, value] of rows) {
    const tr = node("tr");
    tr.append(node("th", "", t(key)));
    for (const p of catalog) tr.append(node("td", "", value(p)));
    tbody.append(tr);
  }
  table.append(thead, tbody);
}
function changeLanguage() {
  language = $("language").value;
  try {
    localStorage.setItem("omni-language", language);
  } catch {}
  translatePage();
  renderForm();
  renderRecent();
  renderLibrary();
  renderTasks();
  renderReference();
  if (selectedJob) renderPreview(selectedJob);
  renderWorkspace();
}
function renderWorkspace() {
  $("workspaceInfo").textContent = workspace
    ? `${t(workspace.storage === "cloud" ? "cloud" : "local")} · ${workspace.workspace} · v${workspace.version}`
    : t("loading");
}
$("generate").addEventListener("click", submit);
$("exampleButton").addEventListener("click", loadExample);
$("language").value = language;
$("language").addEventListener("change", changeLanguage);
$("settingsButton").addEventListener("click", () => {
  renderWorkspace();
  $("settingsDialog").showModal();
});
$("closeInspector").addEventListener("click", () => $("inspector").close());
$("closeManage").addEventListener("click", () => $("manageDialog").close());
$("manageWork").addEventListener("click", openManage);
$("manageForm").addEventListener("submit", (e) => {
  e.preventDefault();
  manage({
    title: $("workTitle").value.trim(),
    collection: $("workCollection").value.trim(),
    notes: $("workNotes").value,
  });
});
$("archiveWork").addEventListener("click", () =>
  manage({ archived: !selectedJob?.library?.archived }),
);
$("trashWork").addEventListener("click", () =>
  manage({ trashed: !selectedJob?.library?.trashed }),
);
$("favorite").addEventListener("click", () => {
  if (selectedJob)
    updateLibrary(selectedJob, {
      favorite: !selectedJob.library?.favorite,
    }).catch((e) => toast(e.message));
});
$("player").addEventListener("error", () => {
  $("playbackError").hidden = !$("player").getAttribute("src");
});
$("retryPlayback").addEventListener("click", () => {
  if (selectedJob?.video_url) {
    $("player").src = selectedJob.video_url + "?reload=" + Date.now();
    $("player").load();
    $("playbackError").hidden = true;
  }
});
$("player").addEventListener("play", () => {
  document.querySelectorAll("video").forEach((v) => {
    if (v !== $("player")) v.pause();
  });
});
$("allTasks").addEventListener("click", () => showView("tasks"));
$("refreshTasks").addEventListener("click", async () => {
  await refreshJobs();
  if (selectedId) await selectJob(selectedId, $("inspector").open);
});
for (const b of document.querySelectorAll("[data-view]"))
  b.addEventListener("click", () => showView(b.dataset.view));
for (const b of document.querySelectorAll('[data-action="new"]'))
  b.addEventListener("click", () => showView("studio"));
for (const b of document.querySelectorAll("[data-detail]"))
  b.addEventListener("click", () => {
    detailTab = b.dataset.detail;
    comparisonEpoch++;
    document.querySelectorAll("#detailContent video").forEach((v) => v.pause());
    if (selectedJob) renderDetail(selectedJob);
  });
for (const id of ["librarySearch", "libraryFilter", "collectionFilter"])
  $(id).addEventListener("input", () => {
    showLimit = 24;
    renderLibrary();
  });
for (const id of ["taskSearch", "taskFilter"])
  $(id).addEventListener("input", renderTasks);
$("moreWorks").addEventListener("click", () => {
  showLimit += 24;
  renderLibrary();
});
for (const id of [
  "prompt",
  "duration",
  "resolution",
  "aspect",
  "sourceUri",
  "seed",
  "labels",
  "stateful",
  "enhance",
  "audioSpeech",
  "audioMusic",
  "audioEffects",
  "captions",
])
  $(id).addEventListener("input", () => {
    saveDraft();
    if (id === "sourceUri") {
      draft().sourceId = "";
      draft().sourceAsset = null;
      renderSourceOptions();
      persist();
    }
  });
$("sourceJob").addEventListener("change", () => {
  saveDraft();
  draft().sourceUri = "";
  draft().sourceAsset = null;
  renderForm();
  persist();
});
for (const id of ["model", "customModel"])
  $(id).addEventListener("change", () => {
    saveDraft();
    renderForm();
    persist();
  });
$("imageFiles").addEventListener("change", (e) => {
  addFiles("images", [...e.target.files]);
  e.target.value = "";
});
$("videoFiles").addEventListener("change", (e) => {
  addFiles("videos", [...e.target.files]);
  e.target.value = "";
});
$("sourceFile").addEventListener("change", (e) => {
  const file = e.target.files[0];
  if (!file) return;
  if (file.size > 20 * 1024 * 1024) {
    error(t("fileLimit"));
    return;
  }
  saveDraft();
  draft().sourceAsset = { file, name: file.name, mime_type: file.type };
  draft().sourceId = "";
  draft().sourceUri = "";
  e.target.value = "";
  renderForm();
  persist();
});
$("addImageUrl").addEventListener("click", () => {
  const url = window.prompt(t("imageUrl"), "");
  if (!url) return;
  try {
    const parsed = new URL(url);
    if (!["https:", "http:"].includes(parsed.protocol))
      throw new Error("Use an HTTP or HTTPS image URL.");
    saveDraft();
    if (draft().images.length >= imageLimit()) throw new Error(t("tooMany"));
    draft().images.push({
      url,
      name: url.split("/").at(-1),
      mime_type: "image/jpeg",
    });
    renderAttachments();
    persist();
  } catch (e) {
    error(e.message);
  }
});
$("addVideoUri").addEventListener("click", () => {
  const uri = window.prompt(t("videoUri"), "");
  if (!uri) return;
  if (!uri.startsWith("gs://")) {
    error(t("videoUri"));
    return;
  }
  saveDraft();
  if (draft().videos.length >= profile().reference_videos_max) {
    error(t("tooMany"));
    return;
  }
  draft().videos.push({
    uri,
    name: uri.split("/").at(-1),
    mime_type: "video/mp4",
  });
  renderAttachments();
  persist();
});
$("restoreWorkspace").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  try {
    const data = JSON.parse(await file.text());
    if (
      data.format !== "omni-workspace-v1" ||
      !/^[a-f0-9]{64}$/.test(data.recovery_key || "")
    )
      throw new Error(t("invalidRecovery"));
    if (!confirm(t("restoreConfirm"))) return;
    saveDraft();
    await api(
      "/api/workspace",
      jsonOptions("POST", { recovery_key: data.recovery_key }),
    );
    location.reload();
  } catch (err) {
    $("settingsStatus").textContent = err.message;
  } finally {
    e.target.value = "";
  }
});
window.addEventListener("hashchange", () => {
  const next = location.hash.slice(1);
  if (["studio", "library", "tasks"].includes(next) && next !== view)
    showView(next);
});
window.addEventListener("online", refreshJobs);
window.addEventListener("storage", (e) => {
  if (e.key === "omni-language") return;
  refreshJobs();
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden) refreshJobs();
});
window.addEventListener("pagehide", () => {
  if (booted) saveDraft();
});
async function poll() {
  await refreshJobs();
  setTimeout(
    poll,
    [...jobs.values()].some((j) => !terminal.has(j.status)) ? 3000 : 10000,
  );
}
async function boot() {
  translatePage();
  showView(view);
  try {
    workspace = await api("/api/workspace");
    const health = await api("/api/health");
    catalog = health.models || [];
    restoreDrafts();
    renderForm();
    renderWorkspace();
    renderReference();
    booted = true;
    await refreshJobs();
    await settlePending();
    renderRecent();
    let last = null;
    try {
      last = localStorage.getItem("omni-selected-" + workspace.workspace);
    } catch {}
    if (last && jobs.has(last) && !jobs.get(last).library?.trashed)
      selectJob(last, false);
    if (view === "library") renderLibrary();
    if (view === "tasks") renderTasks();
    poll();
  } catch (e) {
    $("connectionBanner").hidden = false;
    $("connectionBanner").textContent = e.message;
    renderForm();
  }
}
boot();
