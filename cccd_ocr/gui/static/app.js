/* Giao diện CCCD OCR — JavaScript thuần, không phụ thuộc thư viện ngoài. */

(() => {
  "use strict";

  /** @type {{id:string, name:string, side:string, remote:boolean}[]} */
  const items = [];
  let scanning = false;
  let pollTimer = null;

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const els = {
    dropzone: $("#dropzone"),
    fileInput: $("#file-input"),
    pick: $("#btn-pick"),
    scan: $("#btn-scan"),
    reset: $("#btn-reset"),
    auto: $("#opt-auto"),
    thorough: $("#opt-thorough"),
    progress: $("#progress"),
    progressLabel: $("#progress-label"),
    progressCount: $("#progress-count"),
    progressCurrent: $("#progress-current"),
    barFill: $("#bar-fill"),
    results: $("#results"),
    resultBody: $("#result-body"),
    resultCount: $("#result-count"),
    xlsx: $("#btn-xlsx"),
    csv: $("#btn-csv"),
    toast: $("#toast"),
    overlay: $("#drop-overlay"),
  };

  const COLUMN_SIDES = ["front", "back", ""];

  // ------------------------------------------------------------------ tiện ích

  function toast(message, isError = false) {
    els.toast.textContent = message;
    els.toast.classList.toggle("error", isError);
    els.toast.hidden = false;
    clearTimeout(toast._timer);
    toast._timer = setTimeout(() => { els.toast.hidden = true; }, isError ? 6000 : 3500);
  }

  async function api(path, options = {}) {
    const response = await fetch(path, options);
    const type = response.headers.get("content-type") || "";
    if (!type.includes("application/json")) {
      throw new Error(`Lỗi máy chủ (${response.status})`);
    }
    const data = await response.json();
    if (!response.ok || data.ok === false) {
      throw new Error(data.error || `Lỗi máy chủ (${response.status})`);
    }
    return data;
  }

  function postJson(path, payload) {
    return api(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
  }

  function readAsDataURL(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(String(reader.result));
      reader.onerror = () => reject(new Error(`Không đọc được ${file.name}`));
      reader.readAsDataURL(file);
    });
  }

  // ---------------------------------------------------------------- trạng thái

  function itemsOf(side) {
    return items.filter((item) => item.side === side);
  }

  function refreshCounts() {
    COLUMN_SIDES.forEach((side) => {
      const badge = document.querySelector(`[data-count="${side}"]`);
      if (badge) badge.textContent = String(itemsOf(side).length);
    });
  }

  function updateButtons() {
    els.scan.disabled = scanning || items.length === 0;
    els.reset.disabled = scanning || items.length === 0;
    els.auto.disabled = scanning;
    els.thorough.disabled = scanning;
  }

  // ----------------------------------------------------------------- vẽ thẻ

  function renderAll() {
    COLUMN_SIDES.forEach((side) => renderColumn(side));
    refreshCounts();
    updateButtons();
  }

  function renderColumn(side) {
    const container = document.querySelector(`[data-list="${side}"]`);
    if (!container) return;
    container.textContent = "";
    itemsOf(side).forEach((item) => container.appendChild(buildCard(item)));
  }

  function buildCard(item) {
    const card = document.createElement("figure");
    card.className = "card" + (item.remote ? "" : " uploading");
    card.draggable = true;
    card.dataset.id = item.id;
    card.title = "Kéo sang cột khác để đổi mặt trước / mặt sau";

    const img = document.createElement("img");
    img.alt = item.name;
    img.loading = "lazy";
    if (item.remote) {
      img.src = `/api/thumb/${item.id}`;
    } else if (item.preview) {
      img.src = item.preview;
      const spinner = document.createElement("div");
      spinner.className = "spinner";
      spinner.textContent = "đang tải…";
      card.appendChild(spinner);
    }
    card.appendChild(img);

    const name = document.createElement("figcaption");
    name.className = "name";
    name.textContent = item.name;
    card.appendChild(name);

    const tools = document.createElement("div");
    tools.className = "tools";

    const flip = document.createElement("button");
    flip.className = "tool flip";
    flip.type = "button";
    flip.textContent = "⇄";
    flip.title = "Đổi mặt: chưa rõ → mặt trước → mặt sau";
    flip.addEventListener("click", (event) => {
      event.stopPropagation();
      const order = ["", "front", "back"];
      moveItem(item.id, order[(order.indexOf(item.side) + 1) % order.length]);
    });
    tools.appendChild(flip);

    if (item.remote) {
      const view = document.createElement("button");
      view.className = "tool view";
      view.type = "button";
      view.textContent = "⤢";
      view.title = "Xem ảnh gốc";
      view.addEventListener("click", (event) => {
        event.stopPropagation();
        window.open(`/api/image/${item.id}`, "_blank", "noopener");
      });
      tools.appendChild(view);
    }

    const remove = document.createElement("button");
    remove.className = "tool remove";
    remove.type = "button";
    remove.textContent = "×";
    remove.title = "Bỏ ảnh này";
    remove.addEventListener("click", (event) => {
      event.stopPropagation();
      removeItem(item.id);
    });
    tools.appendChild(remove);

    card.appendChild(tools);

    card.addEventListener("dragstart", (event) => {
      event.dataTransfer.setData("text/plain", item.id);
      event.dataTransfer.effectAllowed = "move";
      card.classList.add("dragging");
    });
    card.addEventListener("dragend", () => card.classList.remove("dragging"));

    return card;
  }

  // ------------------------------------------------------------- thêm / xoá

  async function addFiles(files, side) {
    const list = Array.from(files).filter((file) => file.type.startsWith("image/")
      || /\.(jpe?g|png|bmp|webp|tiff?)$/i.test(file.name));
    if (list.length === 0) {
      toast("Không thấy ảnh nào trong những tệp vừa chọn", true);
      return;
    }

    const placeholders = list.map((file) => {
      const item = { id: `tmp-${Math.random().toString(36).slice(2)}`, name: file.name,
                     side, remote: false, preview: "" };
      items.push(item);
      return { file, item };
    });
    renderAll();

    let failed = 0;
    await Promise.all(placeholders.map(async ({ file, item }) => {
      try {
        item.preview = await readAsDataURL(file);
        const data = await postJson("/api/upload", {
          name: file.name,
          data: item.preview,
          side,
        });
        item.id = data.id;
        item.remote = true;
      } catch (error) {
        failed += 1;
        const index = items.indexOf(item);
        if (index >= 0) items.splice(index, 1);
      }
    }));

    renderAll();
    if (failed) toast(`${failed} ảnh không tải lên được`, true);
  }

  async function removeItem(id) {
    const index = items.findIndex((item) => item.id === id);
    if (index < 0) return;
    const [removed] = items.splice(index, 1);
    renderAll();
    if (removed.remote) {
      try { await postJson("/api/remove", { id: removed.id }); } catch (_) { /* bỏ qua */ }
    }
  }

  function moveItem(id, side) {
    const item = items.find((entry) => entry.id === id);
    if (!item || item.side === side) return;
    item.side = side;
    renderAll();
    if (item.remote) {
      postJson("/api/side", { id: item.id, side }).catch(() => { /* bỏ qua */ });
    }
  }

  // ------------------------------------------------------------ kéo & thả

  function setupDragAndDrop() {
    // Đếm dragenter/dragleave rất dễ lệch (mỗi phần tử con bắn một sự kiện),
    // làm lớp phủ mờ bị kẹt lại và che cả trang. Dùng hẹn giờ: còn nhận được
    // dragover thì còn hiện, im lặng 150ms thì tự ẩn.
    let hideTimer = null;

    function showOverlay() {
      els.overlay.hidden = false;
      clearTimeout(hideTimer);
      hideTimer = setTimeout(hideOverlay, 150);
    }

    function hideOverlay() {
      clearTimeout(hideTimer);
      els.overlay.hidden = true;
      $$(".column.is-over, .dropzone.is-over").forEach((el) => el.classList.remove("is-over"));
    }

    function carriesFiles(event) {
      return Boolean(event.dataTransfer)
        && Array.from(event.dataTransfer.types).includes("Files");
    }

    document.addEventListener("dragover", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      showOverlay();
    });

    document.addEventListener("dragenter", (event) => {
      if (!carriesFiles(event)) return;
      event.preventDefault();
      showOverlay();
    });

    document.addEventListener("dragleave", (event) => {
      if (!carriesFiles(event)) return;
      // relatedTarget null = đã ra khỏi cửa sổ trình duyệt
      if (event.relatedTarget === null) hideOverlay();
    });

    document.addEventListener("dragend", hideOverlay);

    document.addEventListener("drop", (event) => {
      const files = event.dataTransfer && event.dataTransfer.files;
      const internalId = event.dataTransfer && event.dataTransfer.getData("text/plain");
      event.preventDefault();
      hideOverlay();

      const zone = event.target.closest("[data-side]");
      const side = zone ? zone.dataset.side : "";

      if (files && files.length) {
        addFiles(files, side);
      } else if (internalId) {
        moveItem(internalId, side);
      }
    });

    // Cho phép thả lên từng cột
    $$(".column, .dropzone").forEach((zone) => {
      zone.addEventListener("dragover", (event) => {
        const data = event.dataTransfer;
        if (!data) return;
        const types = Array.from(data.types);
        if (!types.includes("text/plain") && !types.includes("Files")) return;
        event.preventDefault();
        zone.classList.add("is-over");
      });
      zone.addEventListener("dragleave", () => zone.classList.remove("is-over"));
    });
  }

  // ------------------------------------------------------------------ quét

  async function startScan() {
    if (scanning || items.length === 0) return;
    const ready = items.filter((item) => item.remote);
    if (ready.length === 0) {
      toast("Ảnh vẫn đang tải lên, đợi một chút nhé", true);
      return;
    }

    scanning = true;
    updateButtons();
    els.results.hidden = true;
    els.progress.hidden = false;
    els.progressLabel.textContent = "Đang quét…";
    els.progressCount.textContent = `0/${ready.length}`;
    els.progressCurrent.textContent = "";
    els.barFill.style.width = "0%";

    try {
      await postJson("/api/scan", {
        items: ready.map((item) => ({ id: item.id, side: item.side })),
        merge: els.auto.checked,
        thorough: els.thorough.checked,
      });
      poll();
    } catch (error) {
      scanning = false;
      updateButtons();
      els.progress.hidden = true;
      toast(error.message, true);
    }
  }

  function poll() {
    clearTimeout(pollTimer);
    pollTimer = setTimeout(async () => {
      try {
        const status = await api("/api/status");
        const total = status.total || 1;
        const percent = Math.round((status.done / total) * 100);
        els.barFill.style.width = `${percent}%`;
        els.progressCount.textContent = `${status.done}/${status.total}`;
        els.progressCurrent.textContent = status.current
          ? `Đang đọc: ${status.current}`
          : "";
        els.progressLabel.textContent = status.running ? "Đang quét…" : "Đã quét xong";

        if (status.running) {
          poll();
          return;
        }

        scanning = false;
        updateButtons();
        if (status.error) {
          toast(`Lỗi: ${status.error}`, true);
          els.progress.hidden = true;
          return;
        }
        renderResults(status.rows || []);
        setTimeout(() => { els.progress.hidden = true; }, 900);
      } catch (error) {
        scanning = false;
        updateButtons();
        toast(error.message, true);
      }
    }, 700);
  }

  // --------------------------------------------------------------- kết quả

  const COLUMNS = [
    ["id_number", "Số CCCD"], ["full_name", "Họ và tên"],
    ["date_of_birth", "Ngày sinh"], ["sex", "Giới tính"],
    ["nationality", "Quốc tịch"], ["place_of_origin", "Quê quán"],
    ["place_of_residence", "Nơi thường trú"], ["date_of_issue", "Ngày cấp"],
    ["date_of_expiry", "Có giá trị đến"], ["personal_identification", "Đặc điểm nhận dạng"],
    ["issue_authority", "Nơi cấp"],
  ];

  function renderResults(rows) {
    els.resultBody.textContent = "";
    rows.forEach((row, index) => {
      const tr = document.createElement("tr");
      const warnings = row.warnings || [];
      if (warnings.length) tr.classList.add("has-warning");
      tr.dataset.warnings = JSON.stringify(warnings);
      tr.dataset.sources = JSON.stringify(row.sources || []);
      tr.dataset.confidence = String(row.confidence || 0);

      const stt = document.createElement("td");
      stt.className = "col-stt";
      const sources = row.sources || [];
      stt.innerHTML = sources.length > 1
        ? `${index + 1}<span class="src-badge" title="${sources.join(' + ')}">2 mặt</span>`
        : String(index + 1);
      tr.title = sources.length ? `Nguồn: ${sources.join(" + ")}` : "";
      tr.appendChild(stt);

      COLUMNS.forEach(([field, label]) => {
        const td = document.createElement("td");
        td.dataset.field = field;
        td.textContent = row[field] || "";
        td.contentEditable = "true";
        td.spellcheck = false;
        td.title = label;
        tr.appendChild(td);
      });

      const conf = document.createElement("td");
      conf.className = "col-conf";
      const score = Number(row.confidence || 0);
      const level = score >= 0.85 ? "good" : score >= 0.7 ? "mid" : "bad";
      conf.innerHTML = `<span class="pill ${level}">${score.toFixed(2)}</span>`;
      tr.appendChild(conf);

      const warn = document.createElement("td");
      warn.className = "col-warn";
      warn.textContent = warnings.join(" · ");
      tr.appendChild(warn);

      els.resultBody.appendChild(tr);
    });

    els.resultCount.textContent = `(${rows.length} dòng)`;
    els.results.hidden = rows.length === 0;
    if (rows.length) {
      els.results.scrollIntoView({ behavior: "smooth", block: "start" });
    }
  }

  function collectRows() {
    return Array.from(els.resultBody.querySelectorAll("tr")).map((tr) => {
      const row = {
        warnings: JSON.parse(tr.dataset.warnings || "[]"),
        sources: JSON.parse(tr.dataset.sources || "[]"),
        confidence: Number(tr.dataset.confidence || 0),
      };
      tr.querySelectorAll("td[data-field]").forEach((td) => {
        row[td.dataset.field] = td.innerText.replace(/\s+/g, " ").trim();
      });
      return row;
    });
  }

  async function download(fmt) {
    const rows = collectRows();
    if (rows.length === 0) {
      toast("Chưa có dữ liệu để tải", true);
      return;
    }
    try {
      const response = await fetch("/api/export", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ fmt, rows }),
      });
      if (!response.ok) {
        const data = await response.json().catch(() => ({}));
        throw new Error(data.error || `Lỗi máy chủ (${response.status})`);
      }
      const blob = await response.blob();
      const url = URL.createObjectURL(blob);
      const link = document.createElement("a");
      link.href = url;
      link.download = `cccd.${fmt}`;
      document.body.appendChild(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 4000);
      toast(`Đã tải cccd.${fmt}`);
    } catch (error) {
      toast(error.message, true);
    }
  }

  // ------------------------------------------------------------------ reset

  async function resetAll() {
    items.length = 0;
    els.results.hidden = true;
    els.progress.hidden = true;
    renderAll();
    try { await postJson("/api/reset", {}); } catch (_) { /* bỏ qua */ }
    els.fileInput.value = "";
  }

  // ------------------------------------------------------------------- gắn

  async function restoreSession() {
    try {
      const state = await api("/api/state");
      (state.uploads || []).forEach((upload) => {
        items.push({ id: upload.id, name: upload.name, side: upload.side || "",
                     remote: true });
      });
      renderAll();
      if (state.rows && state.rows.length) renderResults(state.rows);
    } catch (_) {
      /* phiên mới, không có gì để khôi phục */
    }
  }

  function bind() {
    els.pick.addEventListener("click", (event) => {
      event.stopPropagation();
      els.fileInput.click();
    });
    els.dropzone.addEventListener("click", () => els.fileInput.click());
    els.fileInput.addEventListener("change", () => {
      if (els.fileInput.files.length) addFiles(els.fileInput.files, "");
      els.fileInput.value = "";
    });

    els.scan.addEventListener("click", startScan);
    els.reset.addEventListener("click", resetAll);
    els.xlsx.addEventListener("click", () => download("xlsx"));
    els.csv.addEventListener("click", () => download("csv"));

    document.addEventListener("paste", (event) => {
      const files = Array.from(event.clipboardData?.files || []);
      if (files.length) addFiles(files, "");
    });

    // Ctrl+Enter để quét nhanh
    document.addEventListener("keydown", (event) => {
      if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
        event.preventDefault();
        startScan();
      }
    });

    setupDragAndDrop();
  }

  bind();
  renderAll();
  restoreSession();
})();
