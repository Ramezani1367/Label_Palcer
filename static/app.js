const $ = (id) => document.getElementById(id);

const GROUPS = [
  ["iso", "استاندارد"],
  ["sra", "SRA"],
  ["digital", "دیجیتال"],
  ["press", "ورق چاپ"],
  ["us", "آمریکایی"],
];

const state = {
  jobId: null,
  files: [],
  catalog: null,
  settings: null,
  layout: null,
  page: 0,
  zoomMode: "fit",
  zoom: 2,
  images: {},
  seq: 0,
  previewSeq: 0,
  realImage: null,
  demo: false,
  timer: 0,
  projects: [],
  finished: null,
  rejected: [],
};

function defaults() {
  return {
    paper_mode: "auto",
    paper_id: "A4",
    custom_w: 210,
    custom_h: 297,
    enabled_papers: ["A4", "A3", "A2", "SRA3", "13x19", "35x50", "50x70", "70x100"],
    h_gap: 0,
    v_gap: 0,
    alignment: "center",
    rtl: false,
    finish_a4: true,
    finish_order: "both",
    finish_choice: null,
    finish_min_pages: 5,
    fit: "proportional",
    scale: 100,
    use_custom_label_size: false,
    label_w: 86,
    label_h: 48,
    margin_mode: "auto",
    margin: 5,
    crop_enabled: true,
    crop_length: 6,
    crop_offset: 3,
    crop_weight: 0.25,
    mark_mode: "smart",
    marks_on_empty: false,
    use_trimbox: true,
    use_detected_trim: true,
    extra_trim: 0,
    dpi_override: null,
  };
}

function fmt(value) {
  const n = Number(value);
  if (!Number.isFinite(n)) return "—";
  return Math.abs(n - Math.round(n)) < 0.05 ? String(Math.round(n)) : n.toFixed(1);
}

function noteRejected(list) {
  state.rejected = state.rejected || [];
  for (const item of list || []) {
    if (item && !state.rejected.includes(item)) state.rejected.push(item);
  }
}

function toast(message) {
  const el = $("toast");
  el.textContent = message;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, 5200);
}

async function api(url, opts) {
  const res = await fetch(url, opts);
  const type = res.headers.get("content-type") || "";
  if (type.includes("application/json")) {
    const data = await res.json();
    if (!res.ok || data.ok === false) throw new Error(data.error || "خطا");
    return data;
  }
  if (!res.ok) throw new Error("خطای سرور");
  return res;
}

function loadSettings() {
  state.settings = defaults();
  try {
    const saved = JSON.parse(localStorage.getItem("label-placer-settings-v1") || "null");
    if (saved) {
      state.settings = { ...state.settings, ...saved };
      if (!saved.finish_order) state.settings.finish_order = saved.finish_a4 === false ? "none" : "both";
    }
  } catch { /* keep defaults */ }
}

function saveSettings() {
  const copy = { ...state.settings };
  localStorage.setItem("label-placer-settings-v1", JSON.stringify(copy));
}

function persistJob() {
  if (state.jobId) sessionStorage.setItem("label-job", state.jobId);
  sessionStorage.setItem("label-order", JSON.stringify(state.files.map((f) => ({ id: f.id, copies: f.copies }))));
}

function currentSettings() {
  readForm();
  return state.settings;
}

function order() {
  return state.files.map((f) => ({ id: f.id, copies: Number(f.copies) || 0 }));
}

function readForm() {
  const s = state.settings;
  s.paper_mode = document.querySelector("#paper-mode button[aria-pressed='true']")?.dataset.mode || "auto";
  s.custom_w = Number($("custom-w").value) || s.custom_w;
  s.custom_h = Number($("custom-h").value) || s.custom_h;
  s.h_gap = Math.max(0, Number($("h-gap").value) || 0);
  s.v_gap = Math.max(0, Number($("v-gap").value) || 0);
  s.alignment = document.querySelector("#align-grid button.on")?.dataset.align || "center";
  s.rtl = false;
  s.finish_order = document.querySelector("#finish-order button[aria-pressed='true']")?.dataset.order || "both";
  s.finish_choice = finishChoice() || null;
  s.finish_a4 = s.finish_order === "cut" || s.finish_order === "both";
  s.finish_min_pages = Math.max(2, Math.min(40, Number($("finish-min").value) || 5));
  s.fit = document.querySelector("input[name='fit']:checked")?.value || "proportional";
  if ($("barcode-safe")?.checked) {
    $("scale").value = 100;
    $("scale-label").textContent = "100%";
  }
  s.scale = Number($("scale").value) || 100;
  s.use_custom_label_size = $("custom-label").checked;
  s.label_w = Number($("label-w").value) || s.label_w;
  s.label_h = Number($("label-h").value) || s.label_h;
  s.margin_mode = document.querySelector("input[name='margin-mode']:checked")?.value || "auto";
  s.margin = Math.max(0, Number($("margin").value) || 0);
  s.crop_enabled = $("crop-enabled").checked;
  s.crop_length = Number($("crop-length").value) || 0;
  s.crop_offset = Number($("crop-offset").value) || 0;
  s.crop_weight = Number($("crop-weight").value) || 0.25;
  s.mark_mode = document.querySelector("input[name='mark-mode']:checked")?.value || "smart";
  s.marks_on_empty = $("marks-empty").checked;
  s.use_trimbox = $("use-trimbox").checked;
  s.use_detected_trim = $("use-detected").checked;
  s.extra_trim = Math.max(0, Number($("extra-trim").value) || 0);
  const dpi = $("dpi-override").value.trim();
  s.dpi_override = dpi ? Number(dpi) : null;
  if (s.paper_mode === "auto") {
    const enabled = [...document.querySelectorAll("#paper-chips .chip.on")].map((el) => el.dataset.id);
    if (enabled.length) s.enabled_papers = enabled;
  }
  if (s.paper_mode === "orient") {
    const picked = document.querySelector("#paper-chips .chip.on");
    if (picked) s.paper_id = picked.dataset.id;
  }
}

function syncForm() {
  const s = state.settings;
  document.querySelectorAll("#paper-mode button").forEach((btn) => {
    btn.setAttribute("aria-pressed", btn.dataset.mode === s.paper_mode ? "true" : "false");
  });
  $("custom-w").value = s.custom_w;
  $("custom-h").value = s.custom_h;
  $("h-gap").value = s.h_gap;
  $("v-gap").value = s.v_gap;
  document.querySelectorAll("#align-grid button").forEach((btn) => {
    btn.classList.toggle("on", btn.dataset.align === s.alignment);
  });
  document.querySelectorAll("#finish-order button").forEach((btn) => {
    btn.setAttribute("aria-pressed", btn.dataset.order === (s.finish_order || "both") ? "true" : "false");
  });
  $("finish-min").value = s.finish_min_pages || 5;
  updateFinishUi();
  document.querySelectorAll("input[name='fit']").forEach((el) => { el.checked = el.value === s.fit; });
  $("scale").value = s.scale;
  $("scale-label").textContent = `${s.scale}%`;
  $("custom-label").checked = !!s.use_custom_label_size;
  $("label-w").value = s.label_w || "";
  $("label-h").value = s.label_h || "";
  document.querySelectorAll("input[name='margin-mode']").forEach((el) => { el.checked = el.value === s.margin_mode; });
  $("margin").value = s.margin;
  $("crop-enabled").checked = s.crop_enabled !== false;
  $("crop-length").value = s.crop_length;
  $("crop-offset").value = s.crop_offset;
  $("crop-weight").value = s.crop_weight;
  document.querySelectorAll("input[name='mark-mode']").forEach((el) => { el.checked = el.value === s.mark_mode; });
  $("marks-empty").checked = !!s.marks_on_empty;
  $("use-trimbox").checked = s.use_trimbox !== false;
  $("use-detected").checked = s.use_detected_trim !== false;
  $("extra-trim").value = s.extra_trim || 0;
  $("dpi-override").value = s.dpi_override || "";
  $("show-removed").checked = $("ghost-toggle").checked;
  updateCropMm();
  updateModeUi();
}

function updateCropMm() {
  const length = Number($("crop-length").value) || 0;
  const offset = Number($("crop-offset").value) || 0;
  const mm = (pt) => (pt * 25.4 / 72).toFixed(1);
  $("crop-mm").textContent = `طول ${mm(length)} میلی‌متر، فاصله از گوشه ${mm(offset)} میلی‌متر. حاشیه خودکار ≈ ${mm(length + offset + 1.5 * 72 / 25.4)} میلی‌متر.`;
}

function updateModeUi() {
  const mode = document.querySelector("#paper-mode button[aria-pressed='true']")?.dataset.mode || "auto";
  $("custom-size").style.display = mode === "manual" ? "block" : "none";
  $("paper-chips").style.display = mode === "manual" ? "none" : "flex";
  $("preset-row").style.display = mode === "auto" ? "flex" : "none";
  const hints = {
    auto: "کاغذ از دستور صحافی و مصرف ورق درمی‌آید، نه از دو هشدار پشت‌سرهم.",
    orient: "اندازه را شما انتخاب می‌کنید. برنامه فقط عمودی یا افقی را برمی‌گزیند؛ روی A3 جهت کم‌پرتی را برمی‌دارد.",
    manual: "عرض و ارتفاع را دقیق وارد کنید. جهت، همان چیزی است که نوشتید.",
  };
  $("mode-hint").textContent = hints[mode];
  updateFinishUi();
}

function updateFinishUi() {
  const order = document.querySelector("#finish-order button[aria-pressed='true']")?.dataset.order || state.settings?.finish_order || "both";
  const hints = {
    none: "کاغذ فقط از روی مصرف ورق انتخاب می‌شود. روی حاشیه، نام کاغذ و شماره برگ چاپ می‌شود.",
    cut: "اگر ورق بزرگ به حداقل برگ نرسد، A4 انتخاب می‌شود تا برش ویزیتی بگیرد. سلفون خواسته نشده.",
    lam: "کاغذ A3 یا بزرگ‌تر می‌ماند تا سلفون مات ممکن باشد. جهت همان است که پرتی کمتری دارد.",
    both: "اگر هر دو جور شود، همان ورق می‌ماند. اگر ورق بزرگ زیر حد برگ بماند، یک بار بین برش و سلفون انتخاب می‌کنید.",
  };
  const hint = $("finish-hint");
  if (hint) hint.textContent = hints[order] || "";
  const wrap = $("finish-min-wrap");
  if (wrap) wrap.hidden = order === "none" || order === "lam";
}

function renderPapers() {
  const box = $("paper-chips");
  box.replaceChildren();
  if (!state.catalog) return;
  const enabled = new Set(state.settings.enabled_papers);
  const mode = state.settings.paper_mode;
  for (const [group, label] of GROUPS) {
    const papers = state.catalog.papers.filter((p) => p.group === group);
    if (!papers.length) continue;
    const title = document.createElement("div");
    title.className = "group-label";
    title.textContent = label;
    box.appendChild(title);
    for (const paper of papers) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chip";
      btn.dataset.id = paper.id;
      btn.textContent = `${paper.name}  ${fmt(paper.w)}×${fmt(paper.h)}`;
      const on = mode === "orient" ? paper.id === state.settings.paper_id : enabled.has(paper.id);
      btn.classList.toggle("on", on);
      btn.addEventListener("click", () => {
        if (mode === "orient" || state.settings.paper_mode === "orient") {
          state.settings.paper_id = paper.id;
          state.settings.paper_mode = "orient";
          document.querySelectorAll("#paper-chips .chip").forEach((el) => el.classList.toggle("on", el.dataset.id === paper.id));
        } else {
          btn.classList.toggle("on");
          if (![...document.querySelectorAll("#paper-chips .chip.on")].length) btn.classList.add("on");
        }
        scheduleAnalyze();
      });
      box.appendChild(btn);
    }
  }
}

function batchHeader(file) {
  const head = document.createElement("div");
  head.className = "batch-head";
  const title = document.createElement("strong");
  title.textContent = file.batch_name || "لیبل‌ها";
  const sub = document.createElement("span");
  const row = (state.layout?.batches || []).find((item) => item.id === (file.batch_id || "default"));
  sub.textContent = row && row.start_index > 0
    ? `ادامه از لیبل ${row.start_index + 1} · صفحه ${row.start_page + 1}`
    : "شروع چیدمان";
  head.append(title, sub);
  return head;
}

function renderFiles() {
  const list = $("file-list");
  list.replaceChildren();
  const sized = Object.fromEntries((state.layout?.files || []).map((f) => [f.id, f]));
  let lastBatch = null;
  state.files.forEach((file, index) => {
    const batchId = file.batch_id || "default";
    if (batchId !== lastBatch) {
      lastBatch = batchId;
      list.appendChild(batchHeader(file));
    }
    const row = document.createElement("div");
    row.className = "file-row";
    const img = document.createElement("img");
    img.className = "file-thumb";
    img.alt = "";
    img.src = `/api/jobs/${state.jobId}/thumb/${file.id}`;
    const mid = document.createElement("div");
    const name = document.createElement("div");
    name.className = "file-name";
    name.textContent = file.name;
    name.title = file.name;
    const meta = document.createElement("div");
    meta.className = "file-meta";
    const size = document.createElement("span");
    const eff = sized[file.id];
    size.textContent = eff
      ? `${fmt(eff.width_mm)} × ${fmt(eff.height_mm)} mm`
      : `${fmt(file.width_mm)} × ${fmt(file.height_mm)} mm`;
    meta.appendChild(size);
    const kind = document.createElement("span");
    kind.className = "badge";
    kind.textContent = (file.kind || "").toUpperCase();
    meta.appendChild(kind);
    const serial = serialOf(file.name);
    if (serial) {
      const badge = document.createElement("span");
      badge.className = "badge serial";
      badge.textContent = `سریال ${serial}`;
      meta.appendChild(badge);
    }
    if (file.dpi_assumed) {
      const badge = document.createElement("span");
      badge.className = "badge warn";
      badge.textContent = "DPI فرضی";
      meta.appendChild(badge);
    } else if (file.dpi) {
      const badge = document.createElement("span");
      badge.className = "badge";
      badge.textContent = `${fmt(file.dpi)} dpi`;
      meta.appendChild(badge);
    }
    if (eff?.trim_source === "trimbox" || file.trimbox) {
      const badge = document.createElement("span");
      badge.className = "badge ok";
      badge.textContent = "TrimBox";
      meta.appendChild(badge);
    }
    if (eff?.trim_source === "detected") {
      const badge = document.createElement("span");
      badge.className = "badge ok";
      badge.textContent = "مارک بریده شد";
      meta.appendChild(badge);
    }
    mid.append(name, meta);
    const copies = document.createElement("input");
    copies.type = "number";
    copies.min = "0";
    copies.className = "num";
    copies.value = file.copies;
    copies.setAttribute("aria-label", "تعداد کپی");
    copies.addEventListener("input", () => {
      file.copies = Math.max(0, Number(copies.value) || 0);
      updateTotals();
      scheduleAnalyze();
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "btn";
    remove.textContent = "×";
    remove.title = "حذف";
    remove.setAttribute("aria-label", "حذف");
    remove.style.height = "28px";
    remove.addEventListener("click", () => removeFile(file.id));
    const side = document.createElement("div");
    side.className = "file-actions";
    side.append(copies, remove);
    row.append(img, mid, side);
    row.dataset.index = String(index);
    list.appendChild(row);
  });
  updateTotals();
  updateDrop();
}

function updateTotals() {
  $("file-count").textContent = `${state.files.length} فایل`;
  const copies = state.files.reduce((sum, file) => sum + (Number(file.copies) || 0), 0);
  $("copy-count").textContent = `${copies} کپی`;
}

function renderBanner() {
  const layout = state.layout;
  const kicker = $("banner-kicker");
  const title = $("banner-title");
  const size = $("banner-size");
  const meta = $("banner-meta");
  const reason = $("banner-reason");
  title.replaceChildren();
  meta.replaceChildren();
  if (!layout) {
    kicker.textContent = "منتظر لیبل";
    title.textContent = "کاغذ هنوز تشخیص داده نشده";
    size.textContent = "فایل را اضافه کنید تا اندازه و جهت کاغذ انتخاب شود.";
    reason.textContent = "";
    $("banner-continue").textContent = "";
    $("warning-list").replaceChildren();
    $("warn-heading").hidden = true;
    $("page-label").textContent = "صفحه —";
    return;
  }
  const paper = layout.paper;
  kicker.textContent = state.demo ? "نمونه آزمایشی · تشخیص شد" : "تشخیص شد";
  title.append(document.createTextNode(paper.name));
  const orient = document.createElement("span");
  orient.textContent = paper.orientation === "landscape" ? "افقی · Landscape" : paper.orientation === "portrait" ? "عمودی · Portrait" : "مربع";
  title.append(orient);
  size.textContent = `${fmt(paper.width_mm)} × ${fmt(paper.height_mm)} میلی‌متر`;
  const bits = [
    [`${layout.grid.cols}×${layout.grid.rows}`, ""],
    [`${layout.grid.per_page} در برگ`, ""],
    [`${layout.stats.pages} برگ`, ""],
    [`${layout.stats.marks_removed} مارک وسط پاک شد`, "warn"],
  ];
  const batches = layout.batches || [];
  if (batches.length > 1) bits.push([`${batches.length} پوشه، پشت‌سرهم`, "good"]);
  if (layout.finish?.label) bits.push([layout.finish.label, layout.finish.resolved ? "good" : "warn"]);
  for (const [text, kind] of bits) {
    const pill = document.createElement("span");
    pill.className = "pill" + (kind ? " " + kind : "");
    pill.textContent = text;
    meta.appendChild(pill);
  }
  const last = batches[batches.length - 1];
  const slug = layout.slug
    ? `روی برگ، بیرون خط برش: ${layout.slug.base} · برگ ${state.page + 1} از ${layout.stats.pages}`
    : "";
  const pending = layout.finish && !layout.finish.resolved
    ? "برش ویزیتی و سلفون مات با هم جور نشد. یکی را انتخاب کنید تا برگ نهایی شود."
    : "";
  const cont = batches.length > 1 && last
    ? `پوشه‌ها قاطی نشده‌اند. «${last.name}» از لیبل ${last.start_index + 1}، صفحه ${last.start_page + 1} ادامه پیدا می‌کند. نشان برنجی فقط در پیش‌نمایش است و روی PDF چاپ نمی‌شود.`
    : "";
  $("banner-continue").textContent = [pending, slug, cont].filter(Boolean).join(" ");
  reason.textContent = layout.reason || "";
  $("paper-reason").textContent = layout.reason || "";
  $("removed-count").textContent = String(layout.stats.marks_removed);
  $("kept-count").textContent = String(layout.stats.marks_kept);
  $("page-label").textContent = `صفحه ${state.page + 1} از ${layout.pages.length}`;
  const warns = $("warning-list");
  warns.replaceChildren();
  const messages = layout.warnings || [];
  $("warn-heading").hidden = messages.length === 0;
  for (const text of messages) {
    const li = document.createElement("li");
    li.textContent = text;
    warns.appendChild(li);
  }
}

function renderCandidates() {
  const body = $("candidate-table");
  body.replaceChildren();
  const layout = state.layout;
  if (!layout) return;
  for (const item of layout.candidates) {
    const tr = document.createElement("tr");
    tr.className = "clickable";
    const picked = item.width_mm === layout.paper.width_mm && item.height_mm === layout.paper.height_mm;
    if (picked) tr.classList.add("picked");
    const waste = Math.round((1 - item.utilization) * 100);
    for (const text of [item.name, item.orientation_fa, item.per_page, item.pages, `${waste}%`]) {
      const td = document.createElement("td");
      td.textContent = text;
      tr.appendChild(td);
    }
    tr.addEventListener("click", () => {
      state.settings.paper_mode = "manual";
      state.settings.custom_w = item.width_mm;
      state.settings.custom_h = item.height_mm;
      syncForm();
      renderPapers();
      scheduleAnalyze();
    });
    body.appendChild(tr);
  }
}

function naturalKey(name) {
  return String(name).split(/(\d+)/).map((part) => (part.match(/^\d+$/) ? [0, Number(part)] : [1, part.toLowerCase()]));
}

function compareNatural(a, b) {
  const ka = naturalKey(a);
  const kb = naturalKey(b);
  for (let i = 0; i < Math.max(ka.length, kb.length); i++) {
    const pa = ka[i] || [1, ""];
    const pb = kb[i] || [1, ""];
    if (pa[0] !== pb[0]) return pa[0] - pb[0];
    if (pa[1] < pb[1]) return -1;
    if (pa[1] > pb[1]) return 1;
  }
  return 0;
}

function compareFiles(a, b, mode) {
  if (mode === "name") return a.name.localeCompare(b.name, "fa");
  if (mode === "size") return (a.bytes || 0) - (b.bytes || 0);
  return compareNatural(a.name, b.name);
}

function sortFiles() {
  const mode = $("sort-mode").value;
  const groups = [];
  for (const file of state.files) {
    const id = file.batch_id || "default";
    if (!groups.length || groups[groups.length - 1].id !== id) groups.push({ id, files: [] });
    groups[groups.length - 1].files.push(file);
  }
  state.files = groups.flatMap((group) => group.files.sort((a, b) => compareFiles(a, b, mode)));
  renderFiles();
  scheduleAnalyze();
}

function placementCount(files) {
  return files.reduce((sum, file) => sum + Math.max(0, Number(file.copies) || 0), 0);
}

function serialOf(name) {
  const matches = String(name || "").match(/\d+/g);
  if (!matches) return "";
  return matches.sort((a, b) => b.length - a.length || Number(b) - Number(a))[0];
}

function nextBatchIndex() {
  return state.files.reduce((max, file) => Math.max(max, Number(file.batch_index) || 0), 0) + 1;
}

function groupIncoming(files) {
  const map = new Map();
  for (const file of files) {
    const rel = file.webkitRelativePath || "";
    const parts = rel.split("/").filter(Boolean);
    const folder = parts.length > 1 ? parts[0] : "";
    const key = folder || "__loose__";
    if (!map.has(key)) map.set(key, []);
    map.get(key).push(file);
  }
  let index = nextBatchIndex() - 1;
  const stamp = Date.now().toString(36);
  return [...map.entries()].map(([key, list]) => {
    index += 1;
    list.sort((a, b) => compareNatural(a.name, b.name));
    return {
      id: `b${stamp}${index}`,
      name: key === "__loose__" ? `افزودن ${index}` : key,
      index,
      files: list,
    };
  });
}

async function readEntries(reader) {
  const all = [];
  while (true) {
    const batch = await new Promise((resolve, reject) => reader.readEntries(resolve, reject));
    if (!batch.length) return all;
    all.push(...batch);
  }
}

async function walkEntry(entry, prefix, collected) {
  if (entry.isFile) {
    const file = await new Promise((resolve, reject) => entry.file(resolve, reject));
    const rel = prefix ? `${prefix}/${file.name}` : file.name;
    try { Object.defineProperty(file, "webkitRelativePath", { value: rel }); } catch { /* already set */ }
    collected.push(file);
  } else if (entry.isDirectory) {
    const next = prefix ? `${prefix}/${entry.name}` : entry.name;
    for (const child of await readEntries(entry.createReader())) await walkEntry(child, next, collected);
  }
}

async function filesFromDrop(dataTransfer) {
  const items = [...(dataTransfer.items || [])];
  const collected = [];
  if (items.some((item) => item.webkitGetAsEntry)) {
    for (const item of items) {
      const entry = item.webkitGetAsEntry?.();
      if (entry) await walkEntry(entry, "", collected);
    }
  }
  return collected.length ? collected : [...dataTransfer.files];
}

function updateDrop() {
  const continuing = state.files.length > 0 && !state.demo;
  $("drop-title").textContent = continuing ? "پوشه بعدی را رها کنید" : "لیبل‌ها را اینجا رها کنید";
  $("pick-folder").textContent = continuing ? "افزودن پوشه و ادامه" : "انتخاب پوشه";
  $("pick-files").textContent = continuing ? "افزودن فایل و ادامه" : "انتخاب فایل";
}

function loadProjects() {
  try {
    state.projects = JSON.parse(sessionStorage.getItem("label-projects") || "[]");
  } catch {
    state.projects = [];
  }
}

function placedSize() {
  const measured = state.layout?.files || [];
  const placedIds = new Set(state.files.filter((file) => (file.copies || 0) > 0).map((file) => file.id));
  const row = measured.find((file) => placedIds.has(file.id)) || measured[0];
  if (row?.width_mm && row?.height_mm) return { w: row.width_mm, h: row.height_mm };
  const file = state.files.find((item) => (item.copies || 0) > 0) || state.files[0];
  return file ? { w: file.width_mm, h: file.height_mm } : null;
}

function snapshotProject() {
  if (!state.jobId || state.demo || !state.files.length) return;
  const paper = state.layout?.paper;
  const ref = state.layout?.reference;
  const names = [...new Set(state.files.map((file) => file.batch_name).filter(Boolean))];
  const entry = {
    id: state.jobId,
    name: names[0] || "پروژه",
    settings: JSON.parse(JSON.stringify(state.settings)),
    order: state.files.map((file) => ({ id: file.id, copies: file.copies })),
    summary: paper ? `${paper.name} ${paper.orientation_fa}` : "",
    label: ref ? `${fmt(ref.width_mm)}×${fmt(ref.height_mm)}` : "",
  };
  const index = state.projects.findIndex((item) => item.id === entry.id);
  if (index >= 0) state.projects[index] = entry;
  else state.projects.push(entry);
  sessionStorage.setItem("label-projects", JSON.stringify(state.projects));
  renderProjects();
}

function renderProjects() {
  const bar = $("project-bar");
  if (!bar) return;
  if (state.projects.length < 2) {
    bar.hidden = true;
    bar.replaceChildren();
    return;
  }
  bar.hidden = false;
  bar.replaceChildren();
  const label = document.createElement("span");
  label.textContent = "پروژه‌ها";
  bar.appendChild(label);
  for (const project of state.projects) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "chip" + (project.id === state.jobId ? " on" : "");
    btn.textContent = project.label ? `${project.name} · ${project.label}` : project.name;
    btn.title = [project.name, project.label, project.summary].filter(Boolean).join(" · ");
    btn.addEventListener("click", () => {
      switchProject(project.id).catch((err) => toast(err.message));
    });
    bar.appendChild(btn);
  }
}

async function switchProject(id) {
  if (!id || id === state.jobId) return;
  snapshotProject();
  const project = state.projects.find((item) => item.id === id);
  const job = await api("/api/jobs/" + id);
  state.jobId = id;
  state.demo = false;
  state.page = 0;
  state.images = {};
  state.layout = null;
  state.realImage = null;
  state.finished = null;
  state.rejected = [];
  if (project?.settings) {
    state.settings = { ...defaults(), ...project.settings };
    syncForm();
    renderPapers();
  }
  const byId = Object.fromEntries(job.files.map((file) => [file.id, file]));
  const ordered = [];
  for (const item of project?.order || []) {
    const file = byId[item.id];
    if (!file) continue;
    ordered.push({ ...file, copies: item.copies ?? file.copies ?? 1 });
    delete byId[item.id];
  }
  for (const file of job.files) {
    if (byId[file.id]) ordered.push({ ...file, copies: file.copies ?? 1 });
  }
  state.files = ordered;
  persistJob();
  loadThumbs();
  renderFiles();
  renderProjects();
  await analyze();
}

async function measureGroup(group, placed) {
  readForm();
  const body = new FormData();
  body.append("settings", JSON.stringify(state.settings));
  if (placed) {
    body.append("reference_w", String(placed.w));
    body.append("reference_h", String(placed.h));
  }
  if ($("all-pages").checked) body.append("all_pages", "1");
  for (const file of group.files) body.append("files", file, file.name);
  return api("/api/measure", { method: "POST", body });
}

function askNewProject(group, placed, odd) {
  return new Promise((resolve) => {
    const dialog = $("size-ask");
    const sizes = [];
    for (const file of odd) {
      const text = `${fmt(file.width_mm)} × ${fmt(file.height_mm)}`;
      if (!sizes.includes(text)) sizes.push(text);
      if (sizes.length === 3) break;
    }
    $("size-ask-text").textContent = `این لیبل‌ها هم‌اندازه پوشه قبلی که چیدم نیستند. در پوشه «${group.name}» ${odd.length} لیبل فرق دارد.`;
    const dl = $("size-ask-sizes");
    dl.replaceChildren();
    for (const [label, value] of [
      ["پوشه قبلی", `${fmt(placed.w)} × ${fmt(placed.h)} میلی‌متر`],
      [`پوشه «${group.name}»`, `${sizes.join(" ، ")} میلی‌متر`],
    ]) {
      const dt = document.createElement("dt");
      dt.textContent = label;
      const dd = document.createElement("dd");
      dd.textContent = value;
      dl.append(dt, dd);
    }
    let settled = false;
    const finish = (yes) => {
      if (settled) return;
      settled = true;
      dialog.close();
      resolve(yes);
    };
    $("size-ask-yes").onclick = () => finish(true);
    $("size-ask-no").onclick = () => finish(false);
    dialog.oncancel = (event) => {
      event.preventDefault();
      finish(false);
    };
    dialog.showModal();
    $("size-ask-yes").focus();
  });
}

async function commitGroups(groups) {
  const rejected = [];
  for (const group of groups) {
    const body = new FormData();
    body.append("batch_id", group.id);
    body.append("batch_name", group.name);
    body.append("batch_index", String(group.index));
    if ($("all-pages").checked) body.append("all_pages", "1");
    for (const file of group.files) body.append("files", file, file.name);
    const data = await api(`/api/jobs/${state.jobId}/files`, { method: "POST", body });
    rejected.push(...(data.rejected || []));
    noteRejected(data.rejected);
    const byId = Object.fromEntries(data.files.map((file) => [file.id, file]));
    const kept = state.files.filter((file) => byId[file.id]).map((file) => ({
      ...byId[file.id],
      copies: file.copies,
    }));
    const fresh = (data.added || []).map((file) => ({ ...file, copies: file.copies ?? 1 }));
    state.files = kept.concat(fresh);
  }
  state.images = {};
  loadThumbs();
  renderFiles();
  return rejected;
}

async function openFreshJob() {
  const job = await api("/api/jobs", { method: "POST" });
  state.jobId = job.id;
  state.files = [];
  state.images = {};
  state.demo = false;
  state.layout = null;
  state.page = 0;
  state.realImage = null;
  state.finished = null;
  state.rejected = [];
  renderJobReport();
}

async function startSeparateProject(groups) {
  snapshotProject();
  await openFreshJob();
  state.settings = { ...state.settings, paper_mode: "auto", use_custom_label_size: false };
  syncForm();
  renderPapers();
  const fresh = groups.map((group, index) => ({ ...group, index: index + 1 }));
  const rejected = await commitGroups(fresh);
  await analyze();
  const paper = state.layout?.paper;
  const ref = state.layout?.reference;
  if (rejected.length) toast(rejected.slice(0, 2).join(" "));
  else if (paper && ref) {
    toast(`پروژه جدید ساخته شد. لیبل ${fmt(ref.width_mm)}×${fmt(ref.height_mm)} میلی‌متر، کاغذ ${paper.name} ${paper.orientation_fa}.`);
  } else toast("پروژه جدید ساخته شد.");
}

function setCopies(value) {
  const all = $("apply-all")?.checked;
  const last = [...state.files].reverse().find((file) => file.batch_id)?.batch_id;
  for (const file of state.files) {
    if (!all && last && file.batch_id !== last) continue;
    file.copies = value;
  }
  renderFiles();
  scheduleAnalyze();
}

async function removeFile(id) {
  try {
    await api(`/api/jobs/${state.jobId}/files/${id}`, { method: "DELETE" });
  } catch { /* still drop it locally */ }
  state.files = state.files.filter((file) => file.id !== id);
  delete state.images[id];
  renderFiles();
  scheduleAnalyze();
}

function loadThumbs() {
  for (const file of state.files) {
    if (state.images[file.id]) continue;
    const img = new Image();
    img.onload = () => {
      state.images[file.id] = img;
      draw();
    };
    img.src = `/api/jobs/${state.jobId}/thumb/${file.id}`;
  }
}

function scheduleAnalyze() {
  clearTimeout(state.timer);
  state.timer = setTimeout(analyze, 160);
}

async function analyze() {
  readForm();
  saveSettings();
  persistJob();
  state.realImage = null;
  if (!state.jobId || !state.files.some((file) => file.copies > 0)) {
    state.layout = state.files.length ? state.layout : null;
    if (!state.files.length) state.layout = null;
    if (!state.files.some((file) => file.copies > 0)) {
      state.layout = null;
      renderBanner();
      renderCandidates();
      renderJobReport();
      draw();
      if (state.files.length) toast("تعداد کپی صفر است.");
      return;
    }
  }
  const seq = ++state.seq;
  document.body.classList.add("busy");
  try {
    const data = await api(`/api/jobs/${state.jobId}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: state.settings, files: order() }),
    });
    if (seq !== state.seq) return;
    state.layout = data;
    if (state.page >= data.pages.length) state.page = 0;
    renderFiles();
    renderBanner();
    renderCandidates();
    draw();
    if ($("real-toggle").checked) schedulePreview();
    if (state.finished) state.finished.stale = true;
    renderJobReport();
    snapshotProject();
    maybeAskFinish();
  } catch (err) {
    toast(err.message);
  } finally {
    document.body.classList.remove("busy");
  }
}

function schedulePreview() {
  clearTimeout(schedulePreview._t);
  schedulePreview._t = setTimeout(loadPreview, 350);
}

async function loadPreview() {
  if (!state.jobId || !state.layout) return;
  const seq = ++state.previewSeq;
  const res = await fetch(`/api/jobs/${state.jobId}/preview`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ settings: state.settings, files: order(), page: state.page }),
  });
  const type = res.headers.get("content-type") || "";
  if (!res.ok || !type.includes("image")) {
    const data = await res.json().catch(() => ({}));
    toast(data.error || "رندر واقعی ناموفق بود");
    return;
  }
  const blob = await res.blob();
  if (seq !== state.previewSeq) return;
  const img = new Image();
  img.onload = () => {
    if (seq !== state.previewSeq) return;
    state.realImage = img;
    draw();
  };
  img.src = URL.createObjectURL(blob);
}

function draw() {
  const canvas = $("sheet");
  const scroll = $("sheet-scroll");
  const empty = $("empty");
  const layout = state.layout;
  if (!layout) {
    empty.hidden = false;
    canvas.hidden = true;
    return;
  }
  empty.hidden = true;
  canvas.hidden = false;
  const paper = layout.paper;
  const pad = 76;
  const availW = Math.max(320, scroll.clientWidth - 8);
  const availH = Math.max(280, scroll.clientHeight - 8);
  let scale = state.zoom;
  if (state.zoomMode === "fit") {
    scale = Math.min((availW - pad * 2) / paper.width_mm, (availH - pad * 2) / paper.height_mm);
    scale = Math.max(0.35, scale || 1);
    state.zoom = scale;
  }
  const sheetW = paper.width_mm * scale;
  const sheetH = paper.height_mm * scale;
  const cssW = Math.max(availW, sheetW + pad * 2);
  const cssH = Math.max(availH, sheetH + pad * 2);
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  canvas.style.width = cssW + "px";
  canvas.style.height = cssH + "px";
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);
  const ox = (cssW - sheetW) / 2;
  const oy = (cssH - sheetH) / 2;

  ctx.save();
  ctx.shadowColor = "rgba(0,0,0,0.45)";
  ctx.shadowBlur = 28;
  ctx.shadowOffsetY = 16;
  ctx.fillStyle = "#f3efe4";
  ctx.fillRect(ox, oy, sheetW, sheetH);
  ctx.restore();
  ctx.strokeStyle = "#ddd4c4";
  ctx.strokeRect(ox + 0.5, oy + 0.5, sheetW - 1, sheetH - 1);

  if ($("real-toggle").checked && state.realImage) {
    ctx.drawImage(state.realImage, ox, oy, sheetW, sheetH);
  } else {
    const page = layout.pages[state.page];
    if (page) drawPage(ctx, page, ox, oy, scale);
  }
  drawDimension(ctx, ox, oy + sheetH + 18, sheetW, `${fmt(paper.width_mm)} mm`, true);
  drawDimension(ctx, ox - 18, oy, sheetH, `${fmt(paper.height_mm)} mm`, false);
}

function drawPage(ctx, page, ox, oy, scale) {
  const showRemoved = $("ghost-toggle").checked && $("show-removed").checked;
  for (const cell of page.cells) {
    const x = ox + cell.x * scale;
    const y = oy + cell.y * scale;
    const w = cell.w * scale;
    const h = cell.h * scale;
    ctx.save();
    ctx.beginPath();
    ctx.rect(x, y, w, h);
    ctx.clip();
    if (cell.empty) {
      ctx.fillStyle = "rgba(28,24,20,0.04)";
      ctx.fillRect(x, y, w, h);
    } else {
      const img = state.images[cell.file_id];
      const file = state.files.find((item) => item.id === cell.file_id);
      const dest = cell.content || cell;
      const dx = ox + dest.x * scale;
      const dy = oy + dest.y * scale;
      const dw = dest.w * scale;
      const dh = dest.h * scale;
      if (img && file) {
        const trim = cell.trim || { left: 0, top: 0, right: 0, bottom: 0 };
        const sx = (trim.left / file.width_mm) * img.width;
        const sy = (trim.top / file.height_mm) * img.height;
        const sw = Math.max(1, img.width - sx - (trim.right / file.width_mm) * img.width);
        const sh = Math.max(1, img.height - sy - (trim.bottom / file.height_mm) * img.height);
        ctx.drawImage(img, sx, sy, sw, sh, dx, dy, dw, dh);
      } else {
        ctx.fillStyle = "#cbbba6";
        ctx.fillRect(x, y, w, h);
      }
    }
    ctx.restore();
    ctx.strokeStyle = "rgba(28,24,20,0.28)";
    ctx.lineWidth = 1;
    ctx.strokeRect(x + 0.5, y + 0.5, w - 1, h - 1);
    if (!cell.empty && cell.batch_start && cell.batch_index > 0) drawBatchFlag(ctx, x, y, w, h, cell);
  }
  const weight = Math.max(1, (state.layout.crop.weight_pt || 0.25) * scale * (25.4 / 72));
  if (showRemoved) {
    ctx.save();
    ctx.strokeStyle = "rgba(210,74,54,0.85)";
    ctx.setLineDash([4, 3]);
    ctx.lineWidth = 1;
    for (const mark of page.removed_marks || []) strokeMark(ctx, mark, ox, oy, scale);
    ctx.restore();
  }
  ctx.save();
  ctx.strokeStyle = "#16130f";
  ctx.lineWidth = weight;
  ctx.lineCap = "butt";
  for (const mark of page.marks || []) strokeMark(ctx, mark, ox, oy, scale);
  ctx.restore();
  drawSlug(ctx, ox, oy, scale);
}

function drawSlug(ctx, ox, oy, scale) {
  const slug = state.layout?.slug;
  const layout = state.layout;
  if (!slug || !slug.w || !slug.h || !layout) return;
  const text = `${slug.base} · برگ ${state.page + 1} از ${layout.stats.pages}`;
  ctx.save();
  ctx.beginPath();
  ctx.rect(ox + slug.x * scale, oy + slug.y * scale, slug.w * scale, slug.h * scale);
  ctx.clip();
  ctx.fillStyle = "#16130f";
  const px = Math.max(11, (slug.size_pt || 6.5) * scale * (25.4 / 72));
  ctx.font = `600 ${px}px Vazirmatn, Tahoma, sans-serif`;
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const cx = ox + (slug.x + slug.w / 2) * scale;
  const cy = oy + (slug.y + slug.h / 2) * scale;
  ctx.translate(cx, cy);
  if (slug.rotate) ctx.rotate(-slug.rotate * Math.PI / 180);
  const limit = ((slug.rotate ? slug.h : slug.w) * scale) - 6;
  const width = ctx.measureText(text).width;
  if (width > limit && width > 0) ctx.scale(limit / width, limit / width);
  ctx.fillText(text, 0, 0);
  ctx.restore();
}

function drawBatchFlag(ctx, x, y, w, h, cell) {
  const tabW = Math.min(128, Math.max(72, w * 0.38));
  const tabH = Math.min(18, Math.max(14, h * 0.14));
  ctx.save();
  ctx.fillStyle = "#c6a15a";
  ctx.fillRect(x, y, 3, h);
  ctx.fillRect(x, y, tabW, tabH);
  ctx.fillStyle = "#1c1814";
  ctx.font = "600 11px Vazirmatn, Tahoma, sans-serif";
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  const label = `ادامه ${cell.batch_index + 1}`;
  ctx.fillText(label, x + tabW / 2, y + tabH / 2 + 0.5);
  ctx.restore();
}

function strokeMark(ctx, mark, ox, oy, scale) {
  ctx.beginPath();
  ctx.moveTo(ox + mark.x1 * scale, oy + mark.y1 * scale);
  ctx.lineTo(ox + mark.x2 * scale, oy + mark.y2 * scale);
  ctx.stroke();
}

function drawDimension(ctx, x, y, length, label, horizontal) {
  ctx.save();
  ctx.strokeStyle = "#c6a15a";
  ctx.fillStyle = "#e6d3a4";
  ctx.lineWidth = 1;
  ctx.font = "12px Vazirmatn, Tahoma, sans-serif";
  ctx.beginPath();
  if (horizontal) {
    ctx.moveTo(x, y);
    ctx.lineTo(x + length, y);
    ctx.moveTo(x, y - 4);
    ctx.lineTo(x, y + 4);
    ctx.moveTo(x + length, y - 4);
    ctx.lineTo(x + length, y + 4);
    ctx.stroke();
    ctx.textAlign = "center";
    ctx.fillText(label, x + length / 2, y + 16);
  } else {
    ctx.moveTo(x, y);
    ctx.lineTo(x, y + length);
    ctx.moveTo(x - 4, y);
    ctx.lineTo(x + 4, y);
    ctx.moveTo(x - 4, y + length);
    ctx.lineTo(x + 4, y + length);
    ctx.stroke();
    ctx.translate(x - 8, y + length / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.textAlign = "center";
    ctx.fillText(label, 0, 0);
  }
  ctx.restore();
}

async function uploadFiles(fileList) {
  const files = [...fileList].filter((file) => file.name && !file.name.startsWith("."));
  if (!files.length) return;
  const replacing = !state.jobId || state.demo;
  if (replacing) {
    await openFreshJob();
    const groups = groupIncoming(files);
    toast("در حال خواندن پوشه…");
    const rejected = await commitGroups(groups);
    if (rejected.length) toast(rejected.slice(0, 3).join(" "));
    await analyze();
    if (!rejected.length) toast("پوشه خوانده شد. اندازه لیبل، کاغذ و جهت کاغذ سنجیده شد.");
    return;
  }

  if (!state.layout) await analyze();
  const placed = placedSize();
  const groups = groupIncoming(files);
  const accepted = [];
  for (let index = 0; index < groups.length; index++) {
    const group = groups[index];
    if (!placed) {
      accepted.push(group);
      continue;
    }
    toast(`اندازه پوشه «${group.name}» با چیدمان قبلی سنجیده می‌شود…`);
    const measured = await measureGroup(group, placed);
    if (measured.rejected?.length) {
      noteRejected(measured.rejected);
      toast(measured.rejected.slice(0, 2).join(" "));
    }
    if (!measured.files?.length) continue;
    const odd = measured.files.filter((file) => file.matches === false);
    if (!odd.length) {
      accepted.push(group);
      continue;
    }
    const yes = await askNewProject(group, placed, odd);
    if (!yes) {
      toast(`پوشه «${group.name}» اضافه نشد. پروژه قبلی دست نخورده ماند.`);
      continue;
    }
    if (accepted.length) {
      const before = placementCount(state.files);
      await commitGroups(accepted);
      await analyze();
      if (state.layout) {
        state.page = Math.min(state.layout.pages.length - 1, Math.floor(before / state.layout.grid.per_page));
        renderBanner();
        draw();
      }
    }
    await startSeparateProject([group]);
    const rest = groups.slice(index + 1).flatMap((item) => item.files);
    if (rest.length) await uploadFiles(rest);
    return;
  }
  if (!accepted.length) return;
  const before = placementCount(state.files);
  toast("پوشه هم‌اندازه است و از اولین خانه خالی چیده می‌شود…");
  const rejected = await commitGroups(accepted);
  if (rejected.length) toast(rejected.slice(0, 3).join(" "));
  await analyze();
  if (!state.layout) return;
  const page = Math.min(state.layout.pages.length - 1, Math.floor(before / state.layout.grid.per_page));
  state.page = page;
  renderBanner();
  draw();
  const names = accepted.map((group) => group.name).join("، ");
  toast(`«${names}» از لیبل ${before + 1}، صفحه ${page + 1} ادامه پیدا کرد. سریال‌ها قاطی نشد.`);
}

async function clearFiles() {
  if (!state.demo && state.files.length) snapshotProject();
  const job = await api("/api/jobs", { method: "POST" });
  state.jobId = job.id;
  state.files = [];
  state.images = {};
  state.layout = null;
  state.demo = false;
  state.finished = null;
  state.rejected = [];
  persistJob();
  renderFiles();
  renderBanner();
  renderCandidates();
  renderJobReport();
  draw();
}

function finishKey() {
  return state.jobId ? `finish-${state.jobId}` : "";
}

function finishChoice() {
  return finishKey() ? sessionStorage.getItem(finishKey()) || "" : "";
}

function setFinishChoice(value) {
  if (!finishKey()) return;
  if (value) sessionStorage.setItem(finishKey(), value);
  else sessionStorage.removeItem(finishKey());
  state.settings.finish_choice = value || null;
}

function askFinish() {
  if (state.finishAsking) return state.finishAsking;
  const conflict = state.layout?.finish?.conflict;
  if (!conflict?.cut || !conflict?.lam) return Promise.resolve("");
  const dialog = $("finish-ask");
  const min = state.layout.finish.min_pages || 5;
  $("finish-ask-text").textContent = `ورق بزرگ ${conflict.lam.pages} برگ می‌شود و زیر ${min} است، پس برش ویزیتی نمی‌گیرد. ${conflict.cut.name} ${conflict.cut.pages} برگ می‌شود، ولی سلفون مات نمی‌گیرد. یکی را انتخاب کنید.`;
  const box = $("finish-ask-options");
  box.replaceChildren();
  const card = (kind, item, title, note) => {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "finish-card";
    btn.append(node("strong", "", title));
    btn.append(node("span", "", `${item.name} ${item.orientation_fa} · ${fmt(item.width_mm)}×${fmt(item.height_mm)} mm`));
    btn.append(node("span", "", `${item.pages} برگ · ${item.per_page} لیبل در برگ · پرتی ${fmt(item.waste_w)}×${fmt(item.waste_h)} mm`));
    btn.append(node("span", "", note));
    return btn;
  };
  const cutNote = conflict.cut.pages >= min
    ? `به حد ${min} برگ می‌رسد. سلفون مات روی این اندازه انجام نمی‌شود.`
    : `هنوز ${conflict.cut.pages} برگ است و زیر حد ${min}. سلفون مات هم انجام نمی‌شود.`;
  const lamNote = conflict.lam.pages >= min
    ? `سلفون مات ممکن است و از حد ${min} برگ هم رد می‌شود.`
    : `سلفون مات ممکن است. زیر ${min} برگ است و برش ویزیتی ممکن است نشود.`;
  const cutBtn = card("cut", conflict.cut, "برش ویزیتی، بدون سلفون", cutNote);
  const lamBtn = card("lam", conflict.lam, "سلفون مات، حتی اگر برگ کم باشد", lamNote);
  box.append(cutBtn, lamBtn);
  state.finishAsking = new Promise((resolve) => {
    let settled = false;
    const finish = (choice) => {
      if (settled) return;
      settled = true;
      dialog.close();
      state.finishAsking = null;
      resolve(choice);
    };
    cutBtn.onclick = () => finish("cut");
    lamBtn.onclick = () => finish("lam");
    dialog.oncancel = (event) => {
      event.preventDefault();
    };
    dialog.showModal();
    lamBtn.focus();
  });
  return state.finishAsking;
}

function maybeAskFinish() {
  const finish = state.layout?.finish;
  if (!finish || finish.resolved || !finish.conflict || state.finishAsking) return;
  if ($("size-ask")?.open || $("finish-ask")?.open) return;
  askFinish().then((choice) => {
    if (!choice) return;
    setFinishChoice(choice);
    analyze().then(() => {
      const picked = state.layout?.slug?.base;
      toast(picked ? `روی برگ چاپ می‌شود: ${picked}` : "دستور صحافی ثبت شد.");
    }).catch((err) => toast(err.message));
  });
}

function node(tag, className, text) {
  const el = document.createElement(tag);
  if (className) el.className = className;
  if (text != null) el.textContent = text;
  return el;
}

function openTab(name) {
  document.querySelector(`.tab[data-tab="${name}"]`)?.click();
}

function mmToPt(mm) {
  return Number(mm) * 72 / 25.4;
}

function lastPageFill(layout) {
  const pages = layout?.pages || [];
  if (!pages.length) return { empty: 0, filled: 0 };
  const last = pages[pages.length - 1];
  const empty = (last.cells || []).filter((cell) => cell.empty).length;
  return { empty, filled: (last.cells || []).length - empty };
}

const ALIGN_FA = {
  center: "وسط برگ",
  topLeft: "بالا چپ",
  topRight: "بالا راست",
  bottomLeft: "پایین چپ",
  bottomRight: "پایین راست",
};
const FIT_FA = {
  proportional: "متناسب، بدون کش آمدن",
  fill: "پر کردن قاب",
  center: "وسط، بدون تغییر اندازه",
};

function jobModel() {
  const layout = state.layout;
  if (!layout) return null;
  const paper = layout.paper;
  const grid = layout.grid;
  const stats = layout.stats;
  const ref = layout.reference || {};
  const crop = layout.crop || {};
  const finished = state.finished;
  const fill = lastPageFill(layout);
  const sized = Object.fromEntries((layout.files || []).map((file) => [file.id, file]));
  const files = state.files.map((file) => {
    const eff = sized[file.id];
    return {
      name: file.name,
      copies: Number(file.copies) || 0,
      kind: (file.kind || "").toUpperCase(),
      serial: serialOf(file.name),
      w: eff?.width_mm ?? file.width_mm,
      h: eff?.height_mm ?? file.height_mm,
      trim: eff?.trim_source || "",
      dpi: file.dpi_assumed ? "DPI فرضی" : (file.dpi ? `${fmt(file.dpi)} dpi` : ""),
      batch: file.batch_name || "",
    };
  });
  const batches = layout.batches || [];
  const batchLines = batches.map((batch) => (
    batch.start_index > 0
      ? `${batch.index + 1}. ${batch.name} — ${batch.count} لیبل — ادامه از لیبل ${batch.start_index + 1}، صفحه ${batch.start_page + 1}`
      : `${batch.index + 1}. ${batch.name} — ${batch.count} لیبل — شروع چیدمان`
  ));
  const fileLines = files.map((file) => {
    const bits = [file.name];
    if (file.serial) bits.push(`سریال ${file.serial}`);
    bits.push(`${fmt(file.w)}×${fmt(file.h)} mm`, `${file.copies} کپی`);
    if (file.batch) bits.push(file.batch);
    return bits.join(" | ");
  });
  const warnings = [...(layout.warnings || [])];
  const errors = (finished?.errors || []).filter((item) => !warnings.includes(item));
  let status = "پیش‌نویس چیدمان — PDF هنوز ساخته نشده";
  if (finished && finished.stale) status = "PDF ساخته شده، ولی چیدمان بعد از آن عوض شده";
  else if (finished && errors.length) status = "PDF ساخته شد، با خطا";
  else if (finished) status = "تمام شد";
  const rival = (layout.candidates || []).find((item) => item.name !== paper.name || item.orientation !== paper.orientation);
  const cropOn = crop.enabled !== false;
  return {
    status,
    final: !!finished && !finished.stale,
    stale: !!finished?.stale,
    when: finished
      ? new Date(finished.at).toLocaleString("fa-IR", { timeZone: "Asia/Tehran", dateStyle: "medium", timeStyle: "short" })
      : "",
    elapsed: finished?.elapsed,
    placed: finished?.placed ?? stats.total_copies,
    copies: stats.total_copies,
    paperName: `${paper.name} ${paper.orientation_fa}`,
    paperSize: `${fmt(paper.width_mm)} × ${fmt(paper.height_mm)} mm  (${fmt(mmToPt(paper.width_mm))} × ${fmt(mmToPt(paper.height_mm))} pt)`,
    finish: layout.finish?.order_fa || "",
    slug: layout.slug ? `${layout.slug.base} · شماره برگ از ${stats.pages}` : "",
    reason: layout.reason || "",
    rival: rival ? `${rival.name} ${rival.orientation_fa}، ${rival.per_page} در برگ، ${rival.pages} برگ` : "",
    labelSize: `${fmt(ref.width_mm)} × ${fmt(ref.height_mm)} mm  (${fmt(mmToPt(ref.width_mm))} × ${fmt(mmToPt(ref.height_mm))} pt)`,
    labelSource: ref.name || "فایل اول",
    cols: grid.cols,
    rows: grid.rows,
    perPage: grid.per_page,
    pages: stats.pages,
    empty: fill.empty,
    filledLast: fill.filled,
    hGap: fmt(grid.h_gap),
    vGap: fmt(grid.v_gap),
    margin: `${fmt(grid.margin)} mm ${state.settings.margin_mode === "manual" ? "(دستی)" : "(خودکار)"}`,
    align: ALIGN_FA[grid.alignment] || grid.alignment,
    fit: FIT_FA[state.settings.fit] || state.settings.fit,
    scale: `${fmt(state.settings.scale)}٪`,
    util: `${Math.round((grid.utilization || 0) * 100)}٪`,
    waste: `${fmt(grid.waste_w)} × ${fmt(grid.waste_h)} mm`,
    area: `${stats.job_area_cm2} cm²`,
    sheet: `${stats.sheet_area_cm2} cm²`,
    cropLine: cropOn
      ? `روشن، ${crop.mode === "block" ? "بلوک" : "هوشمند"}، طول ${fmt(crop.length_pt)} pt، آفست ${fmt(crop.offset_pt)} pt، ضخامت ${fmt(crop.weight_pt)} pt`
      : "خاموش",
    removed: stats.marks_removed,
    kept: stats.marks_kept,
    marksEmpty: state.settings.marks_on_empty ? "بله" : "خیر",
    batches,
    batchLines: batchLines.length ? batchLines : ["پوشه‌ای ثبت نشده"],
    files,
    fileLines: fileLines.length ? fileLines : ["فایلی نیست"],
    warnings,
    errors,
    rejected: state.rejected || [],
    url: finished?.url || "",
  };
}

function reportText(model) {
  const lines = [
    "گزارش نهایی چیدمان هوشمند لیبل",
    "────────────────────────────────",
    `وضعیت: ${model.status}`,
  ];
  if (model.when) lines.push(`زمان: ${model.when}`);
  if (model.elapsed != null) lines.push(`مدت ساخت PDF: ${model.elapsed} ثانیه`);
  lines.push(
    `لیبل چیده‌شده: ${model.placed} از ${model.copies}`,
    `فایل: ${model.files.length}`,
    `پوشه: ${model.batches.length}`,
    "",
    `کاغذ: ${model.paperName}`,
    `اندازه برگ: ${model.paperSize}`,
    `مصرف کاغذ: ${model.area}  (هر برگ ${model.sheet})`,
    `بهره‌وری شبکه: ${model.util}`,
    `پرتی برگ: ${model.waste}`,
    `دستور صحافی: ${model.finish || "—"}`,
    `روی برگ: ${model.slug || "—"}`,
    `دلیل انتخاب: ${model.reason}`,
  );
  if (model.rival) lines.push(`گزینه بعدی: ${model.rival}`);
  lines.push(
    "",
    `اندازه لیبل: ${model.labelSize}`,
    `مرجع اندازه: ${model.labelSource}`,
    `گرید: ${model.cols} ستون × ${model.rows} ردیف`,
    `در هر برگ: ${model.perPage}`,
    `تعداد برگ: ${model.pages}`,
    `برگ آخر: ${model.filledLast} پر، ${model.empty} خانه خالی`,
    `فاصله: افقی ${model.hGap} mm ، عمودی ${model.vGap} mm`,
    `حاشیه: ${model.margin}`,
    `جای شبکه: ${model.align}`,
    `تناسب: ${model.fit}`,
    `مقیاس: ${model.scale}`,
    "ترتیب: ردیف بالا از چپ به راست، ردیف بعد دوباره از چپ",
    "",
    `کراپ‌مارک: ${model.cropLine}`,
    `مارک وسط حذف‌شده: ${model.removed}`,
    `مارک باقی‌مانده: ${model.kept}`,
    `مارک خانه خالی: ${model.marksEmpty}`,
    "",
    "پوشه‌ها:",
    ...model.batchLines,
    "",
    "فایل‌ها:",
    ...model.fileLines,
  );
  lines.push("", model.warnings.length ? "هشدار:" : "هشدار: ندارد");
  lines.push(...model.warnings.map((item) => `— ${item}`));
  if (model.errors.length) {
    lines.push("", "خطای خروجی:");
    lines.push(...model.errors.map((item) => `— ${item}`));
  }
  if (model.rejected.length) {
    lines.push("", "فایل ردشده:");
    lines.push(...model.rejected.map((item) => `— ${item}`));
  }
  return lines.filter((line, index, all) => !(line === "" && all[index - 1] === "")).join("\n");
}

function addRows(parent, rows) {
  const box = node("div", "rep-rows");
  for (const [label, value] of rows) {
    if (value == null || value === "") continue;
    const row = node("div", "rep-row");
    row.append(node("span", "", label), node("b", "", String(value)));
    box.appendChild(row);
  }
  parent.appendChild(box);
}

function renderReportBody(parent, model) {
  parent.replaceChildren();
  parent.append(node("p", "rep-kicker", model.final ? "گزارش نهایی" : "گزارش چیدمان"));
  parent.append(node("h2", "rep-title", model.status));
  if (model.when) parent.append(node("p", "rep-note", model.when + (model.elapsed != null ? ` · ساخت PDF ${model.elapsed} ثانیه` : "")));
  if (model.stale) parent.append(node("p", "rep-note", "برای گزارش نهاییِ هم‌خوان با چیدمان فعلی، PDF را دوباره بسازید."));

  const stats = node("div", "rep-stats");
  for (const [value, label] of [
    [model.placed, "لیبل چیده‌شده"],
    [model.pages, "برگ"],
    [`${model.cols}×${model.rows}`, "گرید"],
    [model.removed, "مارک حذف‌شده"],
  ]) {
    const card = node("div", "rep-stat");
    card.append(node("b", "", String(value)), node("span", "", label));
    stats.appendChild(card);
  }
  parent.appendChild(stats);

  const paper = node("section", "rep-block");
  paper.append(node("h3", "", "کاغذ"));
  addRows(paper, [
    ["کاغذ", model.paperName],
    ["اندازه", model.paperSize],
    ["مصرف", `${model.area} · هر برگ ${model.sheet}`],
    ["بهره‌وری", model.util],
    ["پرتی", model.waste],
    ["دستور صحافی", model.finish],
    ["روی برگ", model.slug],
    ["دلیل", model.reason],
    ["گزینه بعدی", model.rival],
  ]);
  parent.appendChild(paper);

  const grid = node("section", "rep-block");
  grid.append(node("h3", "", "شبکه و لیبل"));
  addRows(grid, [
    ["اندازه لیبل", model.labelSize],
    ["مرجع", model.labelSource],
    ["گرید", `${model.cols} ستون × ${model.rows} ردیف`],
    ["در هر برگ", String(model.perPage)],
    ["تعداد برگ", String(model.pages)],
    ["برگ آخر", `${model.filledLast} پر، ${model.empty} خانه خالی`],
    ["فاصله", `افقی ${model.hGap} mm · عمودی ${model.vGap} mm`],
    ["حاشیه", model.margin],
    ["جای شبکه", model.align],
    ["تناسب", model.fit],
    ["مقیاس", model.scale],
    ["ترتیب", "بالا چپ به راست، ردیف بعد از چپ"],
  ]);
  parent.appendChild(grid);

  const crop = node("section", "rep-block");
  crop.append(node("h3", "", "کراپ‌مارک"));
  addRows(crop, [
    ["تنظیم", model.cropLine],
    ["حذف‌شده", String(model.removed)],
    ["باقی‌مانده", String(model.kept)],
    ["خانه خالی", model.marksEmpty],
  ]);
  parent.appendChild(crop);

  const folders = node("section", "rep-block");
  folders.append(node("h3", "", "پوشه‌ها"));
  for (const line of model.batchLines) {
    const row = node("div", "rep-batch");
    row.append(node("b", "", line));
    folders.appendChild(row);
  }
  parent.appendChild(folders);

  const files = node("section", "rep-block");
  files.append(node("h3", "", "فایل‌ها"));
  for (const file of model.files) {
    const row = node("div", "rep-file");
    const meta = [
      file.serial ? `سریال ${file.serial}` : "",
      `${fmt(file.w)}×${fmt(file.h)} mm`,
      `${file.copies} کپی`,
      file.kind,
      file.dpi,
      file.trim === "trimbox" ? "TrimBox" : (file.trim === "detected" ? "مارک بریده شد" : ""),
    ].filter(Boolean).join(" · ");
    row.append(node("b", "", file.name), node("span", "", meta));
    files.appendChild(row);
  }
  parent.appendChild(files);

  const warns = node("section", "rep-block");
  warns.append(node("h3", "", "هشدار و ردشده"));
  if (!model.warnings.length && !model.errors.length && !model.rejected.length) {
    warns.append(node("p", "rep-ok", "هشداری نیست."));
  } else {
    const list = node("ul", "rep-warn");
    for (const item of [...model.errors, ...model.warnings, ...model.rejected]) {
      list.append(node("li", "", item));
    }
    warns.appendChild(list);
  }
  parent.appendChild(warns);

  const logBlock = node("section", "rep-block");
  logBlock.append(node("h3", "", "متن گزارش، مثل اسکریپت"));
  logBlock.append(node("pre", "rep-log", reportText(model)));
  parent.appendChild(logBlock);
}

function renderJobReport() {
  const tab = document.querySelector('.tab[data-tab="report"]');
  if (tab) tab.classList.toggle("ready", !!state.finished && !state.finished.stale);
  const panel = $("job-report");
  const dialogBody = $("report-body");
  const model = jobModel();
  if (!model) {
    const empty = node("p", "rep-empty", "بعد از چیدمان، گزارش اینجا ساخته می‌شود. با دانلود PDF، گزارش نهایی مثل اسکریپت باز می‌شود.");
    if (panel) panel.replaceChildren(empty);
    if (dialogBody) dialogBody.replaceChildren();
    return;
  }
  if (panel) {
    const wrap = node("div");
    renderReportBody(wrap, model);
    const actions = node("div", "rep-actions");
    const copy = node("button", "btn", "کپی متن");
    copy.type = "button";
    copy.addEventListener("click", () => copyReport());
    const save = node("button", "btn", "ذخیره متن");
    save.type = "button";
    save.addEventListener("click", () => saveReport());
    actions.append(copy, save);
    if (model.url) {
      const link = node("a", "btn primary", "دانلود PDF");
      link.href = model.url;
      link.download = "label-imposition.pdf";
      actions.appendChild(link);
    } else {
      const go = node("button", "btn primary", "دانلود PDF و گزارش نهایی");
      go.type = "button";
      go.addEventListener("click", () => exportPdf());
      actions.appendChild(go);
    }
    wrap.appendChild(actions);
    panel.replaceChildren(wrap);
  }
  if (dialogBody) renderReportBody(dialogBody, model);
  const sub = $("report-sub");
  if (sub) sub.textContent = model.when ? `${model.paperName} · ${model.when}` : model.paperName;
  const title = $("report-title");
  if (title) title.textContent = model.final ? "گزارش نهایی چیدمان" : "گزارش چیدمان";
  const download = $("report-download");
  if (download) {
    download.href = model.url || "#";
    download.toggleAttribute("hidden", !model.url);
  }
}

function reportPlain() {
  const model = jobModel();
  if (!model) return "";
  return reportText(model);
}

async function copyReport() {
  const text = reportPlain();
  if (!text) return;
  try {
    await navigator.clipboard.writeText(text);
    toast("متن گزارش کپی شد.");
  } catch {
    const area = document.createElement("textarea");
    area.value = text;
    document.body.appendChild(area);
    area.select();
    document.execCommand("copy");
    area.remove();
    toast("متن گزارش کپی شد.");
  }
}

function saveReport() {
  const text = reportPlain();
  if (!text) return;
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  const link = document.createElement("a");
  link.href = URL.createObjectURL(blob);
  link.download = "label-report.txt";
  document.body.appendChild(link);
  link.click();
  link.remove();
}

function showReport(report, url) {
  const warnings = new Set(state.layout?.warnings || []);
  state.finished = {
    elapsed: report.elapsed,
    placed: report.placed,
    errors: (report.errors || []).filter((item) => !warnings.has(item)),
    url,
    at: new Date().toISOString(),
    stale: false,
  };
  renderJobReport();
  openTab("report");
  $("report").showModal();
}

async function exportPdf() {
  if (!state.jobId || !state.files.length) {
    toast("اول لیبل اضافه کنید.");
    return;
  }
  if (state.layout?.finish && !state.layout.finish.resolved) {
    const choice = await askFinish();
    if (!choice) return;
    setFinishChoice(choice);
    await analyze();
  }
  const btn = $("export-btn");
  btn.disabled = true;
  const previous = btn.textContent;
  btn.textContent = "در حال ساخت…";
  try {
    readForm();
    const data = await api(`/api/jobs/${state.jobId}/export`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ settings: state.settings, files: order() }),
    });
    showReport(data.report, data.url);
    const link = document.createElement("a");
    link.href = data.url;
    link.download = "label-imposition.pdf";
    document.body.appendChild(link);
    link.click();
    link.remove();
  } catch (err) {
    toast(err.message);
  } finally {
    btn.disabled = false;
    btn.textContent = previous;
  }
}

function renderPresets() {
  const select = $("preset-load");
  const presets = JSON.parse(localStorage.getItem("label-placer-presets") || "{}");
  select.replaceChildren();
  const names = Object.keys(presets);
  if (!names.length) {
    const opt = document.createElement("option");
    opt.textContent = "پریستی ذخیره نشده";
    select.appendChild(opt);
    return;
  }
  for (const name of names) {
    const opt = document.createElement("option");
    opt.value = name;
    opt.textContent = name;
    select.appendChild(opt);
  }
}

function bind() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((el) => el.setAttribute("aria-selected", el === tab ? "true" : "false"));
      document.querySelectorAll(".panel").forEach((panel) => panel.classList.remove("active"));
      $("panel-" + tab.dataset.tab).classList.add("active");
    });
  });
  document.querySelectorAll("#finish-order button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#finish-order button").forEach((el) => el.setAttribute("aria-pressed", el === btn ? "true" : "false"));
      state.settings.finish_order = btn.dataset.order;
      setFinishChoice("");
      updateFinishUi();
      scheduleAnalyze();
    });
  });
  document.querySelectorAll("#paper-mode button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#paper-mode button").forEach((el) => el.setAttribute("aria-pressed", el === btn ? "true" : "false"));
      state.settings.paper_mode = btn.dataset.mode;
      updateModeUi();
      renderPapers();
      scheduleAnalyze();
    });
  });
  document.querySelectorAll("[data-preset]").forEach((btn) => {
    btn.addEventListener("click", () => {
      const ids = state.catalog.presets[btn.dataset.preset] || [];
      state.settings.enabled_papers = [...ids];
    state.settings.paper_mode = "auto";
    document.querySelectorAll("#paper-mode button").forEach((el) => el.setAttribute("aria-pressed", el.dataset.mode === "auto" ? "true" : "false"));
      document.querySelectorAll("[data-preset]").forEach((el) => el.classList.toggle("on", el === btn));
      updateModeUi();
      renderPapers();
      scheduleAnalyze();
    });
  });
  $("unlock-btn").addEventListener("click", () => {
    state.settings.paper_mode = "auto";
    syncForm();
    renderPapers();
    scheduleAnalyze();
  });
  document.querySelectorAll("#align-grid button").forEach((btn) => {
    btn.addEventListener("click", () => {
      document.querySelectorAll("#align-grid button").forEach((el) => el.classList.toggle("on", el === btn));
      scheduleAnalyze();
    });
  });
  document.querySelectorAll("[data-gap]").forEach((btn) => {
    btn.addEventListener("click", () => {
      $("h-gap").value = btn.dataset.gap;
      $("v-gap").value = btn.dataset.gap;
      document.querySelectorAll("[data-gap]").forEach((el) => el.classList.toggle("on", el === btn));
      scheduleAnalyze();
    });
  });
  for (const id of ["h-gap", "v-gap", "custom-w", "custom-h", "label-w", "label-h", "margin", "crop-length", "crop-offset", "crop-weight", "extra-trim", "dpi-override", "finish-min"]) {
    $(id).addEventListener("input", () => {
      if (id === "crop-length" || id === "crop-offset") updateCropMm();
      scheduleAnalyze();
    });
  }
  $("scale").addEventListener("input", () => {
    if ($("barcode-safe").checked) {
      $("scale").value = 100;
    }
    $("scale-label").textContent = `${$("scale").value}%`;
    scheduleAnalyze();
  });
  $("barcode-safe").addEventListener("change", () => {
    $("scale").disabled = $("barcode-safe").checked;
    if ($("barcode-safe").checked) {
      $("scale").value = 100;
      $("scale-label").textContent = "100%";
    }
    scheduleAnalyze();
  });
  $("scale").disabled = $("barcode-safe").checked;
  for (const el of document.querySelectorAll("input[name='fit'], input[name='margin-mode'], input[name='mark-mode'], #crop-enabled, #marks-empty, #use-trimbox, #use-detected, #custom-label, #finish-min")) {
    el.addEventListener("change", scheduleAnalyze);
  }
  $("show-removed").addEventListener("change", () => {
    $("ghost-toggle").checked = $("show-removed").checked;
    draw();
  });
  $("ghost-toggle").addEventListener("change", () => {
    $("show-removed").checked = $("ghost-toggle").checked;
    draw();
  });
  $("real-toggle").addEventListener("change", () => {
    state.realImage = null;
    if ($("real-toggle").checked) schedulePreview();
    draw();
  });
  $("sort-mode").addEventListener("change", sortFiles);
  $("apply-btn").addEventListener("click", () => setCopies(Math.max(0, Number($("apply-count").value) || 0)));
  $("reset-btn").addEventListener("click", () => setCopies(1));
  $("zero-btn").addEventListener("click", () => setCopies(0));
  $("clear-btn").addEventListener("click", () => clearFiles().catch((err) => toast(err.message)));
  $("export-btn").addEventListener("click", exportPdf);
  $("pick-files").addEventListener("click", () => $("file-input").click());
  $("pick-folder").addEventListener("click", () => $("folder-input").click());
  $("file-input").addEventListener("change", (event) => {
    uploadFiles(event.target.files).catch((err) => toast(err.message));
    event.target.value = "";
  });
  $("folder-input").addEventListener("change", (event) => {
    uploadFiles(event.target.files).catch((err) => toast(err.message));
    event.target.value = "";
  });
  const drop = $("dropzone");
  const stage = $("stage");
  for (const el of [drop, stage]) {
    el.addEventListener("dragover", (event) => {
      event.preventDefault();
      drop.classList.add("over");
    });
    el.addEventListener("dragleave", () => drop.classList.remove("over"));
    el.addEventListener("drop", (event) => {
      event.preventDefault();
      drop.classList.remove("over");
      filesFromDrop(event.dataTransfer).then((files) => uploadFiles(files)).catch((err) => toast(err.message));
    });
  }
  $("page-prev").addEventListener("click", () => {
    state.page = Math.max(0, state.page - 1);
    state.realImage = null;
    renderBanner();
    draw();
    if ($("real-toggle").checked) schedulePreview();
  });
  $("page-next").addEventListener("click", () => {
    if (!state.layout) return;
    state.page = Math.min(state.layout.pages.length - 1, state.page + 1);
    state.realImage = null;
    renderBanner();
    draw();
    if ($("real-toggle").checked) schedulePreview();
  });
  $("zoom-in").addEventListener("click", () => {
    state.zoomMode = "manual";
    state.zoom *= 1.15;
    draw();
  });
  $("zoom-out").addEventListener("click", () => {
    state.zoomMode = "manual";
    state.zoom = Math.max(0.3, state.zoom / 1.15);
    draw();
  });
  $("zoom-fit").addEventListener("click", () => {
    state.zoomMode = "fit";
    draw();
  });
  $("preset-save").addEventListener("click", () => {
    const name = $("preset-name").value.trim();
    if (!name) return toast("نام پریست را بنویسید.");
    readForm();
    const presets = JSON.parse(localStorage.getItem("label-placer-presets") || "{}");
    presets[name] = state.settings;
    localStorage.setItem("label-placer-presets", JSON.stringify(presets));
    renderPresets();
    $("preset-load").value = name;
    toast("پریست ذخیره شد.");
  });
  $("preset-load").addEventListener("change", () => {
    const presets = JSON.parse(localStorage.getItem("label-placer-presets") || "{}");
    const preset = presets[$("preset-load").value];
    if (!preset) return;
    state.settings = { ...defaults(), ...preset };
    syncForm();
    renderPapers();
    scheduleAnalyze();
  });
  $("preset-delete").addEventListener("click", () => {
    const presets = JSON.parse(localStorage.getItem("label-placer-presets") || "{}");
    delete presets[$("preset-load").value];
    localStorage.setItem("label-placer-presets", JSON.stringify(presets));
    renderPresets();
  });
  $("report-close").addEventListener("click", () => $("report").close());
  $("report-copy").addEventListener("click", () => copyReport());
  $("report-save").addEventListener("click", () => saveReport());
  new ResizeObserver(() => {
    if (state.zoomMode === "fit") draw();
  }).observe($("sheet-scroll"));
}

async function boot() {
  loadSettings();
  loadProjects();
  state.catalog = await api("/api/catalog");
  if (state.catalog.defaults) {
    const fromEngine = { ...state.catalog.defaults };
    if (!fromEngine.finish_order) delete fromEngine.finish_order;
    if (!fromEngine.finish_choice) delete fromEngine.finish_choice;
    state.settings = { ...defaults(), ...fromEngine, ...state.settings };
  }
  syncForm();
  renderPapers();
  renderPresets();
  bind();
  const saved = sessionStorage.getItem("label-job");
  if (saved) {
    try {
      const job = await api("/api/jobs/" + saved);
      const savedOrder = JSON.parse(sessionStorage.getItem("label-order") || "[]");
      const byId = Object.fromEntries(job.files.map((file) => [file.id, file]));
      const ordered = [];
      for (const item of savedOrder) {
        const file = byId[item.id];
        if (!file) continue;
        ordered.push({ ...file, copies: item.copies ?? file.copies ?? 1 });
        delete byId[item.id];
      }
      for (const file of job.files) {
        if (byId[file.id]) ordered.push({ ...file, copies: file.copies ?? 1 });
      }
      state.jobId = saved;
      state.files = ordered;
      if (state.files.length) {
        loadThumbs();
        renderFiles();
        analyze();
        return;
      }
    } catch { /* start fresh */ }
  }
}

boot().catch((err) => toast(err.message));
