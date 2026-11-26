const $ = selector => document.querySelector(selector);
let state;
let snapshot;
let dismissedQuestions = "";
let jobLimit = 40;
const selected = new Set();
let visibleJobs = [];
const dialog = $("#dialog");

function node(tag, text, className) {
  const item = document.createElement(tag);
  if (text != null) item.textContent = text;
  if (className) item.className = className;
  return item;
}

function button(text, action, primary = false) {
  const item = node("button", text, primary ? "primary" : "");
  item.type = "button";
  item.onclick = action;
  return item;
}

function link(text, href) {
  const item = node("a", text, "button");
  item.href = href;
  return item;
}

function status(message, error = false) {
  const box = $("#status");
  box.hidden = !message;
  box.className = error ? "error" : "";
  box.textContent = message || "";
}

async function post(route, data) {
  try {
    const upload = data instanceof File;
    const response = await fetch("/api/" + route, {
      method: "POST", headers: {"X-Jobhunt-Token": state.token,
        "Content-Type": upload ? "application/octet-stream" : "application/json"},
      body: upload ? data : JSON.stringify(data)
    });
    const result = await response.json();
    if (!response.ok) throw Error(result.error);
    await refresh();
    return true;
  } catch (error) {
    status(error.message, true);
    return false;
  }
}

function modal(title, content) {
  $("#dialog-title").textContent = title;
  $("#dialog-content").replaceChildren(content);
  if (!dialog.open) dialog.showModal();
}

function field(name, label, type = "text", value = "", required = true, options = []) {
  const wrapper = node("label", label);
  const input = node(options.length ? "select" : type === "textarea" ? "textarea" : "input");
  if (options.length) {
    input.append(new Option("Select an answer", ""), ...options.map(item => new Option(item, item)));
  } else if (type === "textarea") {
    input.rows = 5;
  } else input.type = type;
  input.name = name;
  input.setAttribute("aria-label", label);
  input.required = required;
  input.value = value;
  wrapper.append(input);
  return wrapper;
}

function form(fields, label, callback, onError) {
  const element = node("form");
  element.append(...fields, node("button", label, "primary"));
  element.onsubmit = async event => {
    event.preventDefault();
    const submit = element.querySelector("button");
    submit.disabled = true;
    try { await callback(Object.fromEntries(new FormData(element))); }
    catch (error) { (onError || (message => status(message, true)))(error.message); }
    finally { submit.disabled = false; }
  };
  return element;
}

function editAnswer(row) {
  modal("Edit saved answer", form([field("answer", row.question, "text", row.answer, false)],
    "Save answer", async data => {
      if (await post("answers", {rows: [{question: row.question, answer: data.answer}]})) dialog.close();
    }));
}

async function editTex(job) {
  const response = await fetch("/api/file?kind=tex&edit=1&id=" + job.id);
  const result = await response.json();
  if (!response.ok) return status(result.error, true);
  const code = field("source", "LaTeX source", "textarea", result.source);
  code.querySelector("textarea").className = "code";
  const feedback = node("p");
  feedback.setAttribute("role", "status");
  const content = form([code], "Compile PDF", async data => {
    if (!(await post("compile", {id: job.id, source: data.source}))) {
      feedback.textContent = $("#status").textContent;
      return;
    }
    feedback.textContent = "Compiling résumé…";
    while (state.task.busy && dialog.open && feedback.isConnected) {
      await new Promise(resolve => setTimeout(resolve, 500));
      await refresh();
    }
    if (!feedback.isConnected || !dialog.open) return;
    if (!state.task.error) dialog.close();
    else feedback.textContent = state.task.message;
  }, message => { feedback.textContent = message; });
  content.append(feedback);
  content.append(link("Download LaTeX", "/api/file?kind=tex&id=" + job.id));
  modal("Résumé LaTeX", content);
}

function showHistory(title, events) {
  const list = node("div");
  if (!events.length) list.append(node("p", "No previous events."));
  for (const event of events.slice().reverse()) {
    const row = node("div", null, "row");
    row.append(node("h3", event.status), node("p", new Date(event.at || event.checked).toLocaleString()));
    if (event.found != null) row.append(node("p", event.found + " postings"));
    if (event.detail) row.append(node("p", event.detail));
    list.append(row);
  }
  modal(title, list);
}

async function applicationHistory(job) {
  try {
    const response = await fetch("/api/application-history?id=" + job.id);
    const data = await response.json();
    if (!response.ok) throw Error(data.error);
    showHistory(job.title, data.events);
  } catch (error) { status(error.message, true); }
}

async function showFit(job) {
  try {
    const response = await fetch("/api/fit?id=" + job.id);
    const report = await response.json();
    if (!response.ok) throw Error(report.error);
    const body = node("div");
    body.append(node("p", report.verified === undefined ? "Run tailoring to review this role against your résumé." :
      report.suitable && report.verified ? "The evidence review found support for the role’s essential requirements." :
      "The review identified gaps. Auto-apply uses the experience documented in your résumé."));
    if (report.changed_lines != null) body.append(node("p", report.changed_lines + " résumé bullets updated."));
    for (const row of report.requirements || []) {
      const entry = node("div", null, "row");
      entry.append(node("h3", row.requirement), node("p", row.evidence));
      body.append(entry);
    }
    for (const [heading, entries] of [["Essential gaps", report.gaps], ["Draft edits reverted by the audit", report.reverted]]) {
      if (!entries?.length) continue;
      const list = node("ul");
      list.append(...entries.map(entry => node("li", entry)));
      body.append(node("h3", heading), list);
    }
    modal("Fit review · " + job.title, body);
  } catch (error) { status(error.message, true); }
}

const statusLabels = {"found":"Found", "applied":"Applied", "failed":"Failed", "retry":"Needs retry", "in progress":"In progress"};

function applyReady(job) {
  return !job.removed_at && !job.archived && !job.submitted && !job.submission_pending;
}

function changeStatus(job) {
  modal("Application status", form([
    field("status", "Status", "text", job.submission_pending ? "retry" : job.application_status, true,
      job.submission_pending ? ["retry"] : ["found", "in progress", "failed", "retry"]),
    field("detail", "Notes", "textarea", job.last_application_event?.detail || "", false)
  ], job.submission_pending ? "Confirm no submission and allow retry" : "Save status", async data => {
    if (await post("application-status", {id: job.id, ...data})) dialog.close();
  }));
}

function changeBoard(ids, restoring = false) {
  const action = restoring ? "restore-jobs" : "remove-jobs";
  if (restoring) return post(action, {ids});
  const content = node("div");
  content.append(node("p", "Removed jobs stay in the tracker. You can restore them from the Removed view."));
  content.append(form([], ids.length === 1 ? "Remove job" : "Remove " + ids.length + " jobs", async () => {
    if (await post(action, {ids})) {
      ids.forEach(id => selected.delete(id));
      dialog.close();
      renderJobs();
    }
  }));
  modal("Remove from board", content);
}

function jobRow(job) {
  const row = node("article", null, "row job-card");
  const checkbox = node("input");
  checkbox.type = "checkbox";
  checkbox.checked = selected.has(job.id);
  checkbox.setAttribute("aria-label", "Select " + job.title + " at " + job.company);
  checkbox.onchange = () => {
    if (checkbox.checked) selected.add(job.id);
    else selected.delete(job.id);
    renderSelection();
  };
  const info = node("div");
  const title = node("h3", job.title, "job-title");
  title.append(node("span", job.score + "%", "match-tag"));
  const tag = job.submission_pending ? "Verify submission" : job.removed_at ? "Removed" : job.archived ? "Archived" : statusLabels[job.application_status];
  if (tag && tag !== "Found") title.append(node("span", tag, "tag"));
  info.append(title, node("p", [job.company, job.location].filter(Boolean).join(" · "), "job-company"));
  if (job.reason) info.append(node("p", job.reason, "job-reason"));
  if (job.last_application_event?.detail) info.append(node("p", job.last_application_event.detail, "job-reason"));
  const actions = node("div", null, "job-actions");
  const url = link("View posting", job.url);
  url.target = "_blank";
  url.rel = "noopener noreferrer";
  const run = route => () => {
    if (route === "fill") dismissedQuestions = "";
    return post(route, {id: job.id});
  };
  if (applyReady(job)) {
    actions.append(button(job.application_status === "in progress" ? "Resume auto-apply" : "Auto-apply", run("apply"), true));
    if (state.active_application === job.id) actions.append(button("Scan form", run("fill")));
  }
  if (!job.removed_at) {
    const bookmark = button(job.shortlisted ? "Saved" : "Save", () =>
      post("shortlist", {id: job.id, saved: !job.shortlisted}), false);
    bookmark.classList.add("bookmark");
    bookmark.setAttribute("aria-label", "Save " + job.title + " to shortlist");
    bookmark.setAttribute("aria-pressed", String(job.shortlisted));
    actions.append(bookmark);
  }
  if (job.removed_at) actions.append(button("Restore", () => changeBoard([job.id], true)));
  const menu = node("details", null, "job-menu");
  const toggle = node("summary", "More", "button");
  toggle.setAttribute("aria-label", "More actions for " + job.title);
  const items = node("div", null, "job-menu-items");
  items.append(url, button("Fit review", () => showFit(job)), button(job.tex ? "Regenerate LaTeX" : "Tailor résumé", run("tailor")));
  if (job.tex) items.append(button("Edit LaTeX / compile", () => editTex(job)));
  if (job.pdf) items.append(link("Download PDF", "/api/file?kind=pdf&id=" + job.id));
  if (!job.removed_at) {
    if (!job.confirmed_submission_at) items.append(button(job.submitted ? "Undo applied" : "Mark applied", run("submitted")));
    if (!job.submitted) {
      items.append(button("Open in Chrome", run("open")), button("Update status", () => changeStatus(job)));
    }
    items.append(button("Remove from board", () => changeBoard([job.id])));
  }
  if (job.last_application_event?.at) items.append(button("History", () => applicationHistory(job)));
  items.addEventListener("click", () => { menu.open = false; });
  menu.append(toggle, items);
  actions.append(menu);
  row.append(checkbox, info, actions);
  return row;
}

function questions() {
  const task = state.task;
  const items = task.questions || [];
  if ((!items.length && !task.attention) || dialog.open) return;
  const key = JSON.stringify([task.job_id, items, task.attention]);
  if (key === dismissedQuestions) return;
  dismissedQuestions = key;
  const job = state.jobs.find(row => row.id === task.job_id);
  if (!items.length) {
    const content = node("div");
    content.append(node("p", task.attention));
    if (job) {
      const actions = node("div", null, "actions");
      actions.append(button("Fit review", () => showFit(job)),
        button("Open in Chrome", async () => {
          if (await post("open", {id: job.id})) dialog.close();
        }));
      if (applyReady(job) && state.active_application === job.id) {
        actions.append(button("Scan form for questions", async () => {
          if (await post("fill", {id: job.id})) dialog.close();
        }));
      }
      if (applyReady(job)) actions.append(button("Resume auto-apply", async () => {
        if (await post("apply", {id: job.id})) dialog.close();
      }, true));
      content.append(actions);
    }
    modal(job ? "Action needed · " + job.title : "Action needed", content);
    return;
  }
  const feedback = node("p");
  feedback.setAttribute("role", "status");
  const fields = items.map((item, index) => {
    const type = item.can_draft ? "textarea" :
      ["email", "tel", "url", "date", "number", "textarea"].includes(item.type) ? item.type : "text";
    const label = field(String(index), item.question + (item.required ? "" : " (optional)"),
      type, "", item.required, item.options);
    const input = label.querySelector("input, textarea, select");
    if (item.max_length && ["text", "textarea", "email", "url", "tel"].includes(type)) {
      input.maxLength = item.max_length;
    }
    if (!item.can_draft || !job) return label;
    const container = node("div", null, "draft-field");
    const review = node("div");
    const action = button("Draft from résumé and job", async () => {
      const original = input.value;
      action.disabled = true;
      review.textContent = "Drafting from your résumé and this posting…";
      try {
        if (!await post("draft-answer", {id: job.id, question: item.question})) {
          review.textContent = $("#status").textContent;
          return;
        }
        while (state.task.busy && container.isConnected && dialog.open) {
          await new Promise(resolve => setTimeout(resolve, 600));
          await refresh();
        }
        if (!container.isConnected || !dialog.open) return;
        const draft = state.task.draft;
        if (!draft || draft.job_id !== job.id || draft.question !== item.question) {
          review.textContent = state.task.draft_error || "The application changed. Reopen this question to draft again.";
          return;
        }
        review.replaceChildren(node("p", "Review and edit before saving. This answer is saved for this role."));
        if (input.value === original) input.value = draft.answer;
        else review.append(button("Replace my text with draft", () => { input.value = draft.answer; }));
        const evidence = node("details");
        const list = node("ul");
        list.append(...draft.evidence.map(line => node("li", line)));
        evidence.append(node("summary", "Résumé evidence"), list);
        review.append(evidence);
      } finally {
        action.disabled = false;
      }
    });
    container.append(label, action, review);
    return container;
  });
  const editor = form(fields,
    task.automatic ? "Save and continue" : "Save and fill", async data => {
      const rows = items.map((item, index) => ({question: item.question, answer: data[index]}))
        .filter(row => row.answer.trim());
      if (await post("answer-and-resume", {id: task.job_id, rows})) {
        dialog.close();
        questions();
      } else {
        feedback.textContent = $("#status").textContent;
      }
    });
  editor.append(feedback);
  modal(job ? "Application answers · " + job.title : "Application answers", editor);
}

function renderSelection() {
  const picked = state.jobs.filter(job => selected.has(job.id));
  $("#bulk-bar").hidden = picked.length === 0;
  const offscreen = picked.length - visibleJobs.filter(job => selected.has(job.id)).length;
  $("#selection-count").textContent = picked.length + " selected" + (offscreen ? " · " + offscreen + " outside this view" : "");
  $("#bulk-apply").hidden = !picked.some(applyReady);
  $("#bulk-remove").hidden = !picked.some(job => !job.removed_at);
  $("#bulk-restore").hidden = !picked.some(job => job.removed_at);
  const checked = visibleJobs.filter(job => selected.has(job.id)).length;
  const all = $("#select-page");
  all.disabled = visibleJobs.length === 0;
  all.checked = checked > 0 && checked === visibleJobs.length;
  all.indeterminate = checked > 0 && checked < visibleJobs.length;
}

function updateOptions(id, values) {
  const target = $(id);
  const previous = target.value;
  const first = target.options[0].text;
  target.replaceChildren(new Option(first, ""), ...Array.from(new Set(values.filter(Boolean)))
    .sort((a, b) => a.localeCompare(b)).map(value => new Option(value, value)));
  if (Array.from(target.options).some(option => option.value === previous)) target.value = previous;
}

function renderQueue() {
  const queue = state.application_queue || [];
  const job = state.jobs.find(item => item.id === queue[0]);
  const panel = $("#queue-panel");
  panel.hidden = !job;
  $("#queue-count").textContent = queue.length;
  if (!job) return;
  $("#queue-title").textContent = "Application queue · " + queue.length + " remaining";
  $("#queue-detail").textContent = job.title + " at " + job.company;
  $("#queue-start").textContent = job.submission_pending ? "Verify in Chrome" :
    job.application_status === "in progress" ? "Resume auto-apply" : "Auto-apply next";
  $("#queue-start").onclick = () => post(job.submission_pending ? "open" : "apply", {id: job.id});
  $("#queue-skip").onclick = () => post("skip-queue", {ids: [job.id]});
  $("#queue-fill").hidden = state.active_application !== job.id;
  $("#queue-fill").onclick = () => { dismissedQuestions = ""; return post("fill", {id: job.id}); };
  $("#queue-done").hidden = job.application_status !== "in progress";
  $("#queue-done").onclick = () => post("submitted", {id: job.id});
  panel.querySelectorAll("button").forEach(control => { control.disabled = state.task.busy; });
}

function isOnJobBoard(job) {
  return !job.removed_at && !job.archived && !job.submitted &&
    !["in progress", "applied"].includes(job.application_status);
}

function renderJobs() {
  const mode = $("#job-filter").value;
  const query = $("#job-search").value.trim().toLowerCase();
  const company = $("#job-company").value;
  const location = $("#job-location").value;
  const sort = $("#job-sort").value;
  const jobs = state.jobs.filter(job => {
    const matches = {
      matches: isOnJobBoard(job),
      "in progress": !job.removed_at && !job.submitted && job.application_status === "in progress",
      applied: !job.removed_at && (job.submitted || job.application_status === "applied"),
      shortlist: !job.removed_at && job.shortlisted,
      archived: !job.removed_at && job.archived,
      removed: Boolean(job.removed_at)
    }[mode] ?? (isOnJobBoard(job) && job.application_status === mode);
    return matches && (!company || company === job.company) && (!location || location === job.location) &&
      [job.company, job.title, job.location].join(" ").toLowerCase().includes(query);
  });
  if (sort === "newest") jobs.sort((a, b) => (b.first_seen || "").localeCompare(a.first_seen || ""));
  if (sort === "company") jobs.sort((a, b) => a.company.localeCompare(b.company) || a.title.localeCompare(b.title));
  visibleJobs = jobs.slice(0, jobLimit);
  $("#shortlist-count").textContent = state.jobs.filter(job => job.shortlisted && !job.removed_at).length;
  document.querySelectorAll("[data-view]").forEach(control =>
    control.setAttribute("aria-pressed", String(control.dataset.view === mode)));
  $("#job-count").textContent = "Showing " + visibleJobs.length + " of " + jobs.length + " roles";
  $("#job-list").replaceChildren(...(jobs.length ? visibleJobs.map(jobRow) :
    [node("p", mode === "shortlist" && !state.jobs.some(job => job.shortlisted && !job.removed_at) ? "Save a role to find it here later." : "No roles match these filters.", "empty")]));
  $("#more-jobs").hidden = jobs.length <= jobLimit;
  renderSelection();
}

function renderActivity() {
  const {task, jobs, settings} = state;
  const needsInput = Boolean(task.questions?.length || task.attention);
  const stage = task.stage;
  const unfinished = !stage && !needsInput && !task.busy ?
    jobs.filter(row => !row.removed_at && !row.archived && row.application_status === "in progress")
      .sort((a, b) => (b.last_application_event?.at || "").localeCompare(a.last_application_event?.at || ""))[0] : null;
  const job = unfinished || jobs.find(row => row.id === task.job_id);
  const label = task.busy && stage ? {fit:"Checking fit and résumé", form:"Preparing application form", submit:"Waiting for portal confirmation"}[stage] :
    needsInput ? "Your input is needed" : stage === "complete" ? "Submission confirmed" : unfinished ? "Application in progress" : "Ready to apply";
  $("#activity-label").textContent = label || "Working";
  const title = $("#activity-job");
  title.hidden = !job || (!stage && !needsInput && !unfinished);
  title.textContent = job ? job.title + " · " + job.company : "";
  $("#activity-detail").textContent = task.questions?.length ? "Open the pending questions to continue this application." :
    task.attention || (stage && task.busy ? "Progress updates as Jobhunt moves through the application." :
      stage === "complete" ? "The tracker now shows the confirmed application status." :
      unfinished ? "Resume this application or pick another role from the board." :
      "Choose Auto-apply on a role to start. Jobhunt pauses when it needs you.");
  const stages = ["fit", "form", "submit", "complete"];
  document.querySelectorAll(".flow li").forEach(item => {
    const current = item.dataset.stage === stage;
    item.classList.toggle("done", stages.indexOf(item.dataset.stage) < stages.indexOf(stage));
    if (current) item.setAttribute("aria-current", "step");
    else item.removeAttribute("aria-current");
  });
  const action = $("#activity-action");
  action.hidden = !needsInput && !unfinished;
  action.disabled = task.busy;
  action.textContent = task.questions?.length ? "Answer questions" : needsInput ? "Review next step" :
    unfinished?.submission_pending ? "Verify in Chrome" : "Resume auto-apply";
  action.onclick = () => {
    if (needsInput) { dismissedQuestions = ""; questions(); }
    else if (unfinished) post(unfinished.submission_pending ? "open" : "apply", {id: unfinished.id});
  };
  $("#scan-summary").textContent = settings.enabled ? "Daily scan · " +
    new Date("2000-01-01T" + settings.time + ":00").toLocaleTimeString([], {hour:"numeric", minute:"2-digit"}) + " Eastern" : "";
}

function renderAnswers() {
  if (!state) return;
  const query = $("#answer-search").value.trim().toLowerCase();
  const rows = state.answers.filter(row => [row.question, row.answer].some(value => (value || "").toLowerCase().includes(query)));
  $("#answer-count").textContent = rows.length + " of " + state.answers.length + " saved answers";
  $("#answer-list").replaceChildren(...(rows.length ? rows.map(row => {
    const item = node("div", null, "row row-head");
    const content = node("div");
    content.append(node("h3", row.question), node("p", row.answer));
    item.append(content, button("Edit", () => editAnswer(row)));
    return item;
  }) : [node("p", query ? "No saved answers match your search." : "Saved answers will appear here.", "empty")]));
}

function render() {
  const {jobs, companies, assets, tools, task} = state;
  $("#match-count").textContent = jobs.filter(isOnJobBoard).length;
  $("#pdf-count").textContent = jobs.filter(job => job.pdf && !job.removed_at).length;
  $("#sent-count").textContent = jobs.filter(job => job.submitted).length;
  updateOptions("#job-company", jobs.map(job => job.company));
  updateOptions("#job-location", jobs.map(job => job.location));
  restoreFilters();
  renderQueue();
  renderJobs();
  renderActivity();
  $("#company-list").replaceChildren(...companies.map(row => {
    const item = node("div", null, "row row-head");
    const detail = node("div");
    const source = state.scan_status[row.portal];
    const website = link(row.company, row.portal);
    website.target = "_blank";
    website.rel = "noopener noreferrer";
    detail.append(website, node("p", [source?.status || "Awaiting first scan",
      source?.found == null ? "" : source.found + " postings",
      source?.checked ? new Date(source.checked).toLocaleString() : ""].filter(Boolean).join(" · ")));
    if (source?.detail) detail.append(node("p", source.detail));
    const actions = node("div", null, "actions");
    if (source?.history?.length) actions.append(button("Scan history", () => showHistory(row.company, source.history)));
    actions.append(button("Scan board", () => post("retry-scan", {portal: row.portal})),
      button("Remove", () => post("remove-company", {portal: row.portal})));
    item.append(detail, actions);
    return item;
  }));
  renderAnswers();
  $("#pdf-state").textContent = assets.pdf ? "· Uploaded" : "";
  $("#tex-state").textContent = assets.tex ? "· Uploaded" : "";
  $("#cls-state").textContent = assets.cls ? "· Uploaded" : "· Optional";
  $("#tools").textContent = Object.entries(tools).map(([key, val]) =>
    key + (val ? " available" : " missing")).join(" · ");
  $("#last-scan").textContent = state.settings.last ? "Last scheduled attempt " + state.settings.last : "";
  $("#scan").disabled = task.busy;
  status(task.busy ? (task.stage ? "" : "Working… This can take several minutes.") : task.message, task.error);
  questions();
}

async function refresh() {
  try {
    const response = await fetch("/api/state");
    const payload = await response.json();
    if (!response.ok) throw Error(payload.error);
    const next = JSON.stringify(payload);
    if (next === snapshot) return;
    const previousSettings = JSON.stringify(state?.settings);
    state = payload;
    snapshot = next;
    if (previousSettings !== JSON.stringify(state.settings)) {
      const settings = $("#schedule-form").elements;
      settings.time.value = state.settings.time || "08:00";
      settings.enabled.checked = Boolean(state.settings.enabled);
    }
    if (!state.task.questions?.length && !state.task.attention) dismissedQuestions = "";
    render();
  } catch (error) { status(error.message, true); }
}

function connectForm(selector, route, convert) {
  $(selector).onsubmit = async event => {
    event.preventDefault();
    const data = Object.fromEntries(new FormData(event.currentTarget));
    if (await post(route, convert ? convert(data) : data) && route !== "settings") $(selector).reset();
  };
}
connectForm("#company-form", "company");
connectForm("#answer-form", "answers", data => ({rows: [data]}));
connectForm("#schedule-form", "settings", data => ({time: data.time, enabled: data.enabled === "on"}));
$("#retry-scans").onclick = () => post("retry-scan", {});
$("#more-jobs").onclick = () => { jobLimit += 40; renderJobs(); };
const filterIds = ["#job-filter", "#job-search", "#job-company", "#job-location", "#job-sort"];
const filterStorage = "jobhunt.board-filters";
let restoredFilters = false;
function restoreFilters() {
  if (restoredFilters) return;
  restoredFilters = true;
  try {
    const saved = JSON.parse(localStorage.getItem(filterStorage) || "{}");
    for (const id of filterIds) {
      const control = $(id);
      if (typeof saved[id] === "string" && (control.tagName !== "SELECT" ||
          Array.from(control.options).some(option => option.value === saved[id]))) control.value = saved[id];
    }
  } catch {}
}
function boardFiltersChanged() {
  try { localStorage.setItem(filterStorage, JSON.stringify(Object.fromEntries(filterIds.map(id => [id, $(id).value])))); }
  catch {}
  jobLimit = 40;
  if (state) renderJobs();
}
document.querySelectorAll("[data-view]").forEach(control => {
  control.onclick = () => { $("#job-filter").value = control.dataset.view; boardFiltersChanged(); };
});
$("#answer-search").oninput = renderAnswers;
document.addEventListener("keydown", event => {
  if (event.key !== "/" || event.metaKey || event.ctrlKey || event.altKey || dialog.open ||
      $("#board-workspace").hidden || event.target.closest?.("input, textarea, select, [contenteditable]")) return;
  event.preventDefault();
  $("#job-search").focus();
});

$("#clear-filters").onclick = () => {
  for (const id of filterIds) {
    const control = $(id);
    if (control.tagName === "SELECT") control.selectedIndex = 0;
    else control.value = "";
  }
  boardFiltersChanged();
};
$("#select-page").onchange = event => {
  for (const job of visibleJobs) {
    if (event.target.checked) selected.add(job.id);
    else selected.delete(job.id);
  }
  renderJobs();
};
$("#clear-selection").onclick = () => { selected.clear(); renderJobs(); };
$("#bulk-apply").onclick = async () => {
  const ids = state.jobs.filter(job => selected.has(job.id) && applyReady(job)).map(job => job.id);
  if (ids.length && await post("queue-apply", {ids})) {
    ids.forEach(id => selected.delete(id));
    renderJobs();
  }
};
$("#bulk-remove").onclick = () => changeBoard(state.jobs.filter(job => selected.has(job.id) && !job.removed_at).map(job => job.id));
$("#bulk-restore").onclick = async () => {
  const ids = state.jobs.filter(job => selected.has(job.id) && job.removed_at).map(job => job.id);
  if (ids.length && await changeBoard(ids, true)) {
    ids.forEach(id => selected.delete(id));
    renderJobs();
  }
};
for (const id of filterIds) {
  $(id).oninput = boardFiltersChanged;
}
$("#scan").onclick = () => post("scan", {});
$("#close-dialog").onclick = () => dialog.close();
$("#add-job").onclick = () => modal("Add a job", form([
  field("company", "Company"), field("title", "Role"), field("url", "Job URL", "url"),
  field("description", "Job description", "textarea")
], "Add to queue", async data => { if (await post("job", data)) dialog.close(); }));
for (const type of ["pdf", "tex", "cls"]) {
  $("#base-" + type).onchange = event => {
    const file = event.target.files[0];
    if (file) post("base/" + type, file);
  };
}
function navigation() {
  const links = Array.from(document.querySelectorAll("nav a"));
  const current = links.find(a => a.hash === location.hash) || links[0];
  for (const a of links) {
    $(a.hash).hidden = a !== current;
    if (a === current) a.setAttribute("aria-current", "page");
    else a.removeAttribute("aria-current");
  }
  $("#section-name").textContent = current.lastChild.textContent.trim();
  $("#board-overview").hidden = current.hash !== "#jobs";
  $("#board-workspace").hidden = current.hash !== "#jobs";
}
addEventListener("hashchange", navigation);
navigation();
refresh();
setInterval(refresh, 2000);
