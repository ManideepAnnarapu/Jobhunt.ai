import json
import re
import unicodedata
from urllib.parse import urlsplit

from workflow import DATA, canonical_job, folder, valid_pdf

FIELDS = r"""els => els.flatMap(el => {
  if (el.disabled || el.readOnly || el.getAttribute('autocomplete') === 'one-time-code') return [];
  const custom = el.getAttribute('role') === 'combobox' && el.tagName !== 'SELECT';
  const type = custom ? 'combobox' : el.type;
  if (!['text','email','tel','url','number','textarea','select-one','file','radio','checkbox','date','combobox'].includes(type)) return [];
  const text = node => {
    if (!node) return '';
    if (node.nodeType === Node.TEXT_NODE) return node.textContent;
    if (node.nodeType !== Node.ELEMENT_NODE) return '';
    if (node.matches('input, select, textarea, button, script, style, [hidden], [aria-hidden="true"]')) return '';
    return Array.from(node.childNodes).map(text).join(' ');
  };
  const labels = Array.from(el.labels || []).map(node => text(node).trim()).filter(Boolean).join(' ');
  const legend = text(el.closest('fieldset')?.querySelector('legend'));
  const group = el.closest('[role="group"], [role="radiogroup"]');
  const aria = node => (node?.getAttribute('aria-labelledby') || '').split(/\s+/)
    .map(id => text(document.getElementById(id))).filter(Boolean).join(' ') || node?.getAttribute('aria-label') || '';
  const wrapper = el.closest('[data-field], [data-field-name], [class*="fieldWrapper"], [class*="form-field"], [class*="formField"]');
  const nearby = wrapper?.querySelector('label, [data-label], [class*="fieldLabel"], legend');
  const placeholder = el.placeholder || '';
  const fallback = /^(?:select|choose|type|enter)(?:\s|$)/i.test(placeholder) ? '' : placeholder;
  const question = ((['radio','checkbox'].includes(type) ? legend || aria(group) : '') ||
    labels || aria(el) || text(nearby) || fallback).replace(/\s+/g, ' ').trim();
  const optionLabel = (labels || aria(el) || el.value || '').replace(/\s+/g, ' ').trim();
  if (!question) return [];
  if (!el.checkVisibility() && !(type === 'file' &&
      Array.from(el.labels || []).some(label => label.checkVisibility()))) return [];
  const options = type === 'select-one' ? Array.from(el.options)
    .filter(opt => opt.value && !opt.disabled).map(opt => opt.text.trim()) : [];
  const checked = type === 'radio' || type === 'checkbox';
  const chosen = el.getAttribute('aria-valuetext') || (custom && el.closest('[class*="control"]')?.querySelector('[class*="singleValue"]')?.innerText) || el.value || (custom && el.tagName !== 'INPUT' ? el.innerText : '') || '';
  const isPlaceholder = /^(?:select|choose|please select)(?:\b|…)/i.test(chosen.trim());
  const filled = custom ? Boolean(chosen.trim() && !isPlaceholder) : checked ? el.checked : type === 'file' ? el.files.length > 0 :
    type === 'select-one' ? Boolean(el.value && !el.selectedOptions[0]?.disabled && !/^(?:please )?(?:select|choose)\b/i.test(el.selectedOptions[0]?.text.trim() || '')) : Boolean(el.value);
  return [{type,question,maxLength:el.maxLength > 0 ? el.maxLength : null,fileNames:type === "file" ? Array.from(el.files).map(file => file.name) : [],option:checked ? optionLabel : '', options,filled,required:el.required || el.getAttribute('aria-required') === 'true' ||
      el.closest('[aria-required="true"]') !== null || /[*✱✳∗]\s*$/.test(question)}];
})"""
SELECTOR = 'input, select, textarea, [role="combobox"]'
CONTACT_LABELS = {
    label: canonical
    for canonical, variants in {
        "first name": ("given name", "legal first name"),
        "last name": ("surname", "family name", "legal last name"),
        "email": ("email address", "e-mail", "e-mail address"),
        "phone number": ("phone", "mobile number", "telephone number", "contact phone"),
        "linkedin profile": ("linkedin", "linkedin url", "linkedin profile url"),
    }.items()
    for label in (canonical, *variants)
}
AUTOMATIC_HOSTS = {"jobs.ashbyhq.com", "jobs.lever.co", "jobs.eu.lever.co",
                   "job-boards.greenhouse.io", "boards.greenhouse.io",
                   "job-boards.eu.greenhouse.io", "boards.eu.greenhouse.io"}
SUBMIT = re.compile(r"^(?:submit (?:my |your )?application|send application)$", re.I)
OPEN_FORM = re.compile(r"^(?:apply|apply now|apply for this job|apply for this role|application)$", re.I)
RESUME = re.compile(r"r[eé]sum[eé]|curriculum vitae|\bcv\b", re.I)
ATTESTATION = re.compile(r"\b(?:certify|affirm|consent|acknowledge|attest|signature|privacy policy|agree to)\b", re.I)
CONFIRMATION = r"application (?:has been |was )?(?:submitted|received|sent)|(?:thank you|thanks) for (?:applying|submitting your application)|we(?: have|'ve) received your application"
REMAINING = r"""el => Array.from(el.elements || []).some(node =>
    node.checkVisibility() && !node.disabled && node.willValidate && !node.validity.valid) ||
    Array.from(el.querySelectorAll('[aria-invalid="true"]')).some(node => node.checkVisibility()) ||
    Array.from(el.querySelectorAll('[aria-required="true"]')).some(node => {
      if (!node.checkVisibility() || node.disabled) return false;
      if (['checkbox', 'radio'].includes(node.type)) {
        if (node.type === 'radio' && node.name)
          return !Array.from(el.elements).some(other => other.type === 'radio' && other.name === node.name && other.checked);
        return !node.checked;
      }
      if (node.type === 'file') return !node.files.length;
      if (node.getAttribute('role') === 'combobox' &&
          (node.getAttribute('aria-valuetext') ||
           node.closest('[class*="control"]')?.querySelector('[class*="singleValue"]')?.innerText)) return false;
      if (node.matches('input,select,textarea')) return !node.value.trim();
      return !node.getAttribute('aria-valuetext') && !node.getAttribute('aria-activedescendant') &&
             node.getAttribute('aria-checked') !== 'true';
    })"""



def normalized_option(text):
    value = unicodedata.normalize("NFKC", text).casefold()
    value = re.sub(r"[\u200b-\u200d\ufeff]", "", value)
    value = re.sub(r"[‘’]", "'", value)
    value = value.rstrip(" :?.*✱✳∗")
    value = re.sub(r"(?:\s*\((?:required|optional)\)|\s+required)$", "", value)
    value = " ".join(value.split()).strip(" *?:.")
    return value


def normalized(text):
    value = normalized_option(text)
    return CONTACT_LABELS.get(value, value)


class Browser:
    def __init__(self):
        self.context = None
        self.page = None
        self.job_id = None
        self.dashboard = None

    def show_dashboard(self, url):
        if not self.context:
            return
        if not self.dashboard or self.dashboard.is_closed():
            self.dashboard = self.context.new_page()
        if self.dashboard.url != url + "/":
            self.dashboard.goto(url, wait_until="domcontentloaded", timeout=5000)
        self.dashboard.bring_to_front()

    def start(self, job):
        from playwright.sync_api import sync_playwright
        if not self.context or not self.context.pages:
            if self.context:
                self.context.close()
                self.runtime.stop()
            self.runtime = sync_playwright().start()
            try:
                self.context = self.runtime.chromium.launch_persistent_context(
                    str(DATA / "browser"), channel="chrome", headless=False)
            except Exception:
                self.runtime.stop()
                self.context = None
                raise
            self.context.set_default_timeout(2200)
        if self.job_id == job["id"] and self.page and not self.page.is_closed():
            self.page.bring_to_front()
            return {"message": "Application tab is ready. Open the form, then select Scan form."}
        self.page = self.context.new_page()
        self.job_id = None
        self.page.goto(job["url"], wait_until="domcontentloaded", timeout=25000)
        self.job_id = job["id"]
        return {"message": "Job opened in Chrome. Open the application form, then scan its fields."}

    def application_page(self, job):
        if not self.page or self.job_id != job["id"] or self.page.is_closed():
            raise ValueError("Open this job in the browser assistant first.")
        page = self.page
        # Pick up a portal's same-session pop-up tab after the user opens its form.
        for candidate in reversed(self.context.pages):
            if candidate.opener() == page and urlsplit(candidate.url).scheme == "https":
                page = candidate
                break
        if urlsplit(page.url).scheme != "https":
            raise ValueError("The application page must use HTTPS.")
        return page

    def handoff(self, message):
        return {"message": message, "attention": message}

    def advance(self, job, answers, before_submit, skip_optional=False):
        page = self.application_page(job)
        job_host = urlsplit(job["url"]).hostname
        def current_posting():
            actual = urlsplit(page.url)
            return (actual.scheme == "https" and actual.hostname == job_host
                    and canonical_job(page.url) == canonical_job(job["url"]))
        if job_host not in AUTOMATIC_HOSTS or not current_posting():
            return self.handoff("This portal needs manual navigation or submission. Continue in Chrome.")
        def submit_button():
            return page.get_by_role("button", name=SUBMIT).filter(visible=True)

        submit = submit_button()
        links = (page.get_by_role("link", name=OPEN_FORM)
                 .or_(page.get_by_role("button", name=OPEN_FORM))).filter(visible=True)
        try:
            submit.or_(links).first.wait_for(state="visible", timeout=6500)
        except Exception:
            return self.handoff("Open the application form in Chrome, then resume Jobhunt.")
        if not current_posting():
            return self.handoff("Review the application destination in Chrome.")
        if not submit.count():
            if links.count() != 1:
                return self.handoff("Open the application form in Chrome, then resume Jobhunt.")
            links.click(timeout=4000)
            try:
                submit.first.wait_for(state="visible", timeout=5000)
            except Exception:
                page = self.application_page(job)
                submit = submit_button()
                if not submit.count():
                    return self.handoff("Finish opening the application form in Chrome, then resume Jobhunt.")
        if not current_posting() or submit.count() != 1:
            return self.handoff("Review the application destination and form in Chrome.")
        form = page.locator("form").filter(has=submit)
        if form.count() != 1:
            return self.handoff("This form needs manual review in Chrome before submission.")
        if self.has_confirmation(page) or re.search(r"you (?:have )?already (?:applied|submitted)",
                                                    page.locator("body").inner_text(), re.I):
            return self.handoff("Chrome already shows an application confirmation. Verify it and mark the job applied.")
        for _ in range(3):
            result = self.fill(job, answers, form=form, page=page, skip_optional=skip_optional)
            if not result["filled_count"] or result["failures"]:
                break
        if result["questions"]:
            return result
        if result["failures"]:
            return dict(result, attention=result["message"])
        captcha = page.locator('iframe[src*="hcaptcha"], iframe[title*="reCAPTCHA"], iframe[src*="turnstile"]')
        if any(frame.is_visible() for frame in captcha.all()) or form.evaluate(REMAINING):
            return self.handoff("Complete the remaining required controls or verification in Chrome, then resume.")
        if not result["resume_attached"] or not submit.is_enabled():
            return self.handoff("Confirm the résumé upload and any remaining required fields in Chrome, then resume.")
        if not current_posting():
            return self.handoff("The application destination changed. Review it in Chrome.")
        destination = submit.evaluate("el => el.getAttribute('formaction') ? el.formAction : el.form.action")
        if urlsplit(destination).scheme != "https" or urlsplit(destination).hostname != job_host:
            return self.handoff("The form sends to a different destination. Review and submit it in Chrome.")
        before_submit()
        try:
            submit.click(timeout=8000)
            page.wait_for_function(
                "(pattern) => new RegExp(pattern, 'i').test(document.body?.innerText || '')",
                arg=CONFIRMATION, timeout=12000,
            )
        except Exception:
            return self.handoff("Submission was attempted. Check Chrome for confirmation before retrying.")
        if urlsplit(page.url).hostname != job_host or not self.has_confirmation(page):
            return self.handoff("Submission was attempted. Verify the result in Chrome.")
        return {"message": "The portal confirmed your application.", "submitted": True}

    @staticmethod
    def has_confirmation(page):
        return bool(re.search(CONFIRMATION, page.locator("body").inner_text(), re.I))

    @staticmethod
    def combobox_options(frame, target):
        target.click(timeout=2000)
        ids = (target.get_attribute("aria-controls") or target.get_attribute("aria-owns") or "").split()
        if not ids:
            listbox = frame.get_by_role("listbox").filter(visible=True)
        else:
            listbox = frame.locator("[id=" + json.dumps(ids[0]) + "]").filter(visible=True)
        options = listbox.get_by_role("option").filter(visible=True)
        try:
            options.first.wait_for(state="visible", timeout=1200)
        except Exception:
            target.press("Escape")
            return None, []
        return options, [label.strip() for label in options.all_inner_texts() if label.strip()]

    def fill(self, job, answers, form=None, page=None, skip_optional=False):
        page = page or self.application_page(job)
        saved = {}
        for row in answers:
            key = normalized(row["question"])
            if not key or not row["answer"].strip():
                continue
            answer = row["answer"].strip()
            if key in saved and saved[key] != answer:
                saved[key] = ""
            else:
                saved[key] = answer
        pending = {}
        count = 0
        failures = []
        resume_attached = False
        for frame in [page.main_frame] if form is not None else page.frames:
            if frame != page.main_frame and urlsplit(frame.url).scheme != "https":
                continue
            elements = (form if form is not None else frame).locator(SELECTOR)
            frame_url = frame.url
            handles = elements.element_handles()
            fields = []
            for handle in handles:
                field = handle.evaluate("el => (" + FIELDS + ")([el])[0] || null")
                if field:
                    fields.append(dict(field, handle=handle))
            handled = {normalized(field["question"]) for field in fields
                       if field["filled"] and field["type"] == "radio"}
            for field in fields:
                question = field["question"]
                key = normalized(question)
                kind = field["type"]
                if field["filled"] or (kind == "radio" and key in handled):
                    if kind == "file" and RESUME.search(question):
                        resume_attached = "resume.pdf" in field["fileNames"]
                    continue
                if ATTESTATION.search(question) or re.match(r"^(?:search(?: jobs)?|filter(?: jobs)?|job search)$", key):
                    continue
                if kind == "checkbox" and (field["required"] or sum(
                    other["type"] == "checkbox" and normalized(other["question"]) == key
                    for other in fields
                ) > 1):
                    continue
                target = field["handle"]
                answer = saved.get(key, "")
                options = ["Yes", "No"] if kind == "checkbox" else field["options"]
                if kind == "radio":
                    options = [other["option"] for other in fields
                               if other["type"] == "radio" and normalized(other["question"]) == key
                               and other["option"]]
                choices = None
                if kind == "combobox":
                    try:
                        choices, options = self.combobox_options(frame, target)
                    except Exception:
                        failures.append(f"Review {question} in Chrome.")
                        continue
                    if not options:
                        failures.append(f"Choose an option for {question} in Chrome.")
                        continue
                if kind != "file" and (not answer or options and
                                        not any(normalized_option(option) == normalized_option(answer) for option in options)):
                    if field["required"] or not skip_optional:
                        item = pending.setdefault(key, {"question": question, "options": [], "required": False, "type": kind, "max_length": field["maxLength"]})
                        item["required"] = item["required"] or field["required"]
                        item["options"] = list(dict.fromkeys(item["options"] + options))
                    if choices is not None:
                        target.press("Escape")
                    continue
                try:
                    if frame.url != frame_url:
                        break
                    if kind == "file" and RESUME.search(question):
                        if not valid_pdf(job):
                            failures.append("Compile the current résumé before attaching it.")
                            continue
                        target.set_input_files(str(folder(job) / "resume.pdf"))
                        resume_attached = True
                    elif kind == "file":
                        continue
                    elif kind == "combobox":
                        matches = [item for item in options if normalized_option(item) == normalized_option(answer)]
                        if len(matches) != 1:
                            failures.append(f"Choose an available option for {question} in Chrome.")
                            target.press("Escape")
                            continue
                        choices.and_(frame.get_by_role("option", name=matches[0], exact=True)).click(timeout=2000)
                    elif kind == "select-one":
                        option = next((item for item in field["options"]
                                       if normalized_option(item) == normalized_option(answer)), None)
                        if not option:
                            failures.append(f"Choose an available option for {question}.")
                            continue
                        target.select_option(label=option)
                    elif kind == "radio" and normalized_option(answer) == normalized_option(field["option"]):
                        target.check()
                        handled.add(key)
                    elif kind == "radio":
                        continue
                    elif kind == "checkbox":
                        if normalized(answer) != "yes":
                            continue
                        target.check()
                    else:
                        target.fill(answer)
                    count += 1
                except Exception:
                    failures.append(f"Review {question} in Chrome.")
        return {"questions": list(pending.values()), "filled_count": count, "resume_attached": resume_attached, "failures": list(dict.fromkeys(failures)), "message": " ".join(
            [f"Filled {count} fields. Review the form in Chrome.", *dict.fromkeys(failures)])}
