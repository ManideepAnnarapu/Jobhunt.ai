import csv
import io
import json
import mimetypes
import os
import secrets
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit
from zoneinfo import ZoneInfo

import workflow as work
from answer_drafts import can_draft, draft_answer, role_specific
from browser_assist import Browser, normalized, normalized_option

PORT = int(os.environ.get("JOBHUNT_PORT", "8765"))
ORIGIN = f"http://127.0.0.1:{PORT}"
TOKEN = secrets.token_urlsafe(32)
LOCK = threading.RLock()
TASK = {"busy": False, "message": ""}
WORKER = ThreadPoolExecutor(max_workers=1)
BROWSER_WORKER = ThreadPoolExecutor(max_workers=1)
BROWSER = Browser()


def start_task(operation, executor=WORKER, keep_pause=False):
    with LOCK:
        if TASK["busy"]:
            raise ValueError("Wait for the current task to finish.")
        if not keep_pause:
            TASK.update(questions=[], attention="", automatic=False, draft=None, draft_error="")
        TASK.update(busy=True, message="", stage="")
    def run():
        try:
            result = operation()
            result = result if isinstance(result, dict) else {"message": result}
        except Exception as error:
            result = {"message": str(error), "error": True}
        with LOCK:
            TASK.update(result, busy=False, error=result.get("error", False))
    executor.submit(run)


def scheduler():
    while True:
        try:
            now = datetime.now(ZoneInfo("America/New_York"))
            with LOCK:
                config = work.read_json("settings", {})
                today = now.date().isoformat()
                if (config.get("enabled") and now.strftime("%H:%M") >= config.get("time", "08:00")
                        and config.get("last") != today and not TASK["busy"]):
                    work.write_json("settings", dict(config, last=today))
                    start_task(work.scan, keep_pause=True)
        except Exception as error:
            with LOCK:
                if not TASK["busy"]:
                    TASK.update(message=f"Scheduler: {error}", error=True)
        time.sleep(20)


def application_queue(jobs):
    return [key for key in work.read_json("application-queue", [])
            if key in jobs and eligible_to_apply(jobs[key])]


def eligible_to_apply(job):
    return (work.board_match(job) and not job.get("removed_at")
            and not job.get("submitted") and not job.get("confirmed_submission_at") and not job.get("archived"))


def application_progress(stage, key):
    with LOCK:
        TASK.update(stage=stage, job_id=key, automatic=True)


def prepare_application(job):
    if not work.verified_resume(job):
        work.tailor(job)
    if not work.valid_pdf(job):
        work.compile_resume(job)
    BROWSER.start(job)
    return {}


def save_answers(rows):
    updates = {}
    for row in rows:
        key = normalized(row["question"])
        if key:
            updates[key] = {"question": row["question"], "answer": row["answer"].strip()}
    original = [row for row in work.sheet("answers") if normalized(row["question"]) not in updates]
    return work.sheet("answers", original + [row for row in updates.values() if row["answer"]])


def answers_for_job(job, shared):
    private = work.read_json("role-answers", {}).get(job["id"], {})
    return [row for row in shared if not role_specific(row) and normalized(row["question"]) not in private] + list(private.values())


def save_application_answers(job, pending, supplied):
    private = work.read_json("role-answers", {})
    scoped = private.setdefault(job["id"], {})
    shared = []
    for name, row in supplied.items():
        if role_specific(pending[name]):
            scoped[name] = row
        else:
            shared.append(row)
    work.write_json("role-answers", private)
    return save_answers(shared) if shared else work.sheet("answers")


def run_application(jobs, key, answers, skip_optional=False, queued=False):
    confirmed = 0
    while key:
        job = jobs[key]
        if job.get("submission_pending"):
            message = "A submission was already attempted. Verify the result in Chrome before retrying."
            return {"job_id": key, "automatic": True, "message": message, "attention": message}
        if not eligible_to_apply(job):
            raise ValueError("Choose an active match that you have yet to apply for.")
        def checkpoint():
            application_progress("submit", key)
            job["submission_pending"] = work.timestamp()
            work.record_application(job, "in progress", "Submission attempted. Awaiting portal confirmation.")
            work.write_json("jobs", jobs)
        try:
            application_progress("fit", key)
            result = prepare_application(job)
            if not result.get("attention"):
                application_progress("form", key)
                result = BROWSER.advance(job, answers_for_job(job, answers), checkpoint, skip_optional=skip_optional)
        except Exception as error:
            if job.get("submission_pending"):
                message = "Submission was attempted. Verify the result in Chrome before retrying."
                result = {"message": message, "attention": message}
            else:
                work.record_application(job, "failed", str(error))
                work.write_json("jobs", jobs)
                return {"message": str(error), "attention": str(error), "job_id": key,
                        "automatic": True, "error": True}
        if not result.get("submitted"):
            if not job.get("submission_pending") and BROWSER.job_id == key:
                work.record_application(job, "in progress", result.get("attention", ""))
                work.write_json("jobs", jobs)
            if (result.get("questions") or result.get("attention")) and BROWSER.job_id == key:
                try:
                    BROWSER.show_dashboard(ORIGIN)
                except Exception:
                    pass  # The original dashboard will still receive the question.
            return dict(result, job_id=key, automatic=True)
        job.pop("submission_pending", None)
        job["confirmed_submission_at"] = work.timestamp()
        work.record_application(job, "applied", "Portal confirmed submission.")
        work.write_json("jobs", jobs)
        queue = application_queue(jobs)
        work.write_json("application-queue", queue)
        confirmed += 1
        key = queue[0] if queued and queue else None
        skip_optional = False
    return {"stage": "complete", "message": f"{confirmed} application{'s' if confirmed != 1 else ''} confirmed by the portal."}


def state():
    jobs = []
    stored = work.read_json("jobs", {})
    shortlist = set(work.read_json("shortlist", []))
    for job in stored.values():
        if not work.board_match(job):
            continue
        path = work.folder(job)
        summary = {key: value for key, value in job.items()
                   if key not in {"description", "rank_key", "application_history"}}
        summary["application_status"] = job.get("application_status", "applied" if job.get("submitted") else "found")
        summary["last_application_event"] = next(iter(reversed(job.get("application_history", []))), {})
        jobs.append(dict(summary, shortlisted=job["id"] in shortlist, tex=(path / "resume.tex").is_file(), pdf=work.valid_pdf(job)))
    jobs.sort(key=lambda job: (job.get("archived", False), -job.get("score", 0), job["title"]))
    task = dict(TASK)
    task["questions"] = [dict(item, can_draft=can_draft(item), role_specific=role_specific(item))
                         for item in TASK.get("questions", [])]
    return {"token": TOKEN, "jobs": jobs, **{name: work.sheet(name) for name in work.SHEETS},
            "application_queue": application_queue(stored), "active_application": BROWSER.job_id,
            "scan_status": work.read_json("scan-status", {}),
            "task": task, "settings": work.read_json("settings", {"time": "08:00"}),
            "assets": {**{ext: (work.BASE / ("base." + ext)).is_file() for ext in ("pdf", "tex")},
                       "cls": (work.BASE / "resume.cls").is_file()},
            "tools": {"Codex": bool(os.environ.get("JOBHUNT_CODEX") or work.command("codex")),
                      "LaTeX": bool(work.command("pdflatex"))}}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def send(self, data, kind, status=200, attachment=None):
        self.send_response(status)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; frame-ancestors 'none'; object-src 'none'")
        if attachment:
            self.send_header("Content-Disposition", f'attachment; filename="{attachment}"')
        self.end_headers()
        self.wfile.write(data)

    def json(self, value, status=200):
        self.send(json.dumps(value).encode(), "application/json", status)

    def host_ok(self):
        return self.headers.get("Host") == f"127.0.0.1:{PORT}"

    def do_GET(self):
        if not self.host_ok():
            return self.json({"error": "Invalid host."}, 403)
        parsed = urlsplit(self.path)
        try:
            if parsed.path == "/api/state":
                with LOCK:
                    return self.json(state())
            if parsed.path == "/api/fit":
                job = work.read_json("jobs", {})[parse_qs(parsed.query)["id"][0]]
                return self.json(work.fit_report(job))
            if parsed.path == "/api/application-history":
                key = parse_qs(parsed.query)["id"][0]
                job = work.read_json("jobs", {})[key]
                return self.json({"events": job.get("application_history", [])})
            if parsed.path == "/api/file":
                args = parse_qs(parsed.query)
                name = args["kind"][0]
                if name == "jobs":
                    output = io.StringIO(newline="")
                    columns = ["company", "title", "location", "url", "score", "application_status",
                               "archived", "removed_at", "first_seen", "last_seen", "last_status_at", "last_status_detail"]
                    writer = csv.DictWriter(output, columns, extrasaction="ignore")
                    writer.writeheader()
                    for job in work.read_json("jobs", {}).values():
                        event = next(iter(reversed(job.get("application_history", []))), {})
                        writer.writerow(dict(job, application_status=job.get(
                            "application_status", "applied" if job.get("submitted") else "found"),
                            last_status_at=event.get("at", ""), last_status_detail=event.get("detail", "")))
                    return self.send(output.getvalue().encode(), "text/csv; charset=utf-8", attachment="jobs.csv")
                if name in work.SHEETS:
                    work.sheet(name)
                    return self.send((work.DATA / f"{name}.csv").read_bytes(),
                                     "text/csv; charset=utf-8", attachment=f"{name}.csv")
                job = work.read_json("jobs", {})[args["id"][0]]
                if name not in ("tex", "pdf") or (name == "pdf" and not work.valid_pdf(job)):
                    raise ValueError("File unavailable.")
                payload = (work.folder(job) / f"resume.{name}").read_bytes()
                if name == "tex" and args.get("edit") == ["1"]:
                    return self.json({"source": payload.decode()})
                return self.send(payload, "application/pdf" if name == "pdf" else "text/plain",
                                 attachment=f"resume.{name}")
            static = {"/": "index.html", "/style.css": "style.css", "/ui.js": "ui.js"}
            if parsed.path in static:
                path = work.ROOT / "web" / static[parsed.path]
                return self.send(path.read_bytes(), mimetypes.guess_type(path)[0] + "; charset=utf-8")
            self.json({"error": "Page unavailable."}, 404)
        except (KeyError, OSError, ValueError) as error:
            self.json({"error": str(error)}, 400)

    def do_POST(self):
        if (not self.host_ok() or self.headers.get("Origin") != ORIGIN
                or not secrets.compare_digest(self.headers.get("X-Jobhunt-Token", ""), TOKEN)):
            return self.json({"error": "Reload the dashboard and try again."}, 403)
        try:
            length = int(self.headers["Content-Length"])
            if not 0 < length <= 20_000_000:
                raise ValueError("Upload limit is 20 MB.")
            body = self.rfile.read(length)
            with LOCK:
                if TASK["busy"]:
                    raise ValueError("Wait for the current task to finish.")
                if self.path in ("/api/base/pdf", "/api/base/tex", "/api/base/cls"):
                    ext = self.path.rsplit("/", 1)[1]
                    if ext == "pdf" and not body.startswith(b"%PDF-"):
                        raise ValueError("Select a PDF file.")
                    if ext == "tex" and "\\begin{document}" not in body.decode("utf-8"):
                        raise ValueError("Select a self-contained UTF-8 LaTeX document.")
                    if ext == "cls" and "\\ProvidesClass{resume}" not in body.decode("utf-8"):
                        raise ValueError("Upload the resume.cls file from the original LaTeX project.")
                    work.atomic(work.BASE / ("resume.cls" if ext == "cls" else f"base.{ext}"), body)
                else:
                    self.action(json.loads(body))
            self.json({"ok": True})
        except (KeyError, ValueError, OSError, TypeError) as error:
            self.json({"error": str(error)}, 400)

    def action(self, data):
        route = self.path.removeprefix("/api/")
        if route == "scan":
            start_task(work.scan, keep_pause=True)
        elif route == "retry-scan":
            start_task(lambda: work.scan(portal=data.get("portal"), retry_only=not data.get("portal")),
                       keep_pause=True)
        elif route == "company":
            work.job_url(data["portal"])
            if not data["company"].strip():
                raise ValueError("Enter a company name.")
            rows = [row for row in work.sheet("companies") if row["portal"] != data["portal"]]
            work.sheet("companies", rows + [data])
        elif route == "remove-company":
            rows = [row for row in work.sheet("companies") if row["portal"] != data["portal"]]
            work.sheet("companies", rows)
        elif route == "answers":
            save_answers(data["rows"])
        elif route == "draft-answer":
            key = data["id"]
            item = next((item for item in TASK.get("questions", [])
                         if normalized(item["question"]) == normalized(data["question"])), None)
            if TASK.get("job_id") != key or item is None or not can_draft(item):
                raise ValueError("Choose an open narrative question to delegate.")
            job = work.read_json("jobs", {})[key]
            TASK.update(draft=None, draft_error="")
            def generate_draft():
                try:
                    return {"draft": dict(draft_answer(job, item), job_id=key), "message": ""}
                except Exception as error:
                    return {"draft_error": str(error), "message": ""}
            start_task(generate_draft, keep_pause=True)
        elif route == "answer-and-resume":
            key = data["id"]
            jobs = work.read_json("jobs", {})
            if TASK.get("job_id") != key or not TASK.get("questions"):
                raise ValueError("This application question is no longer active.")
            pending = {normalized(item["question"]): item for item in TASK["questions"]}
            supplied = {}
            for row in data["rows"]:
                name = normalized(row["question"])
                item = pending.get(name)
                answer = row["answer"].strip()
                if not item or not answer:
                    raise ValueError("Answer the questions from the current application.")
                if item.get("max_length") and len(answer) > item["max_length"]:
                    raise ValueError("Shorten the answer to " + item["question"])
                if item["options"] and not any(normalized_option(value) == normalized_option(answer) for value in item["options"]):
                    raise ValueError("Pick an available answer for " + item["question"])
                supplied[name] = {"question": item["question"], "answer": answer}
            if any(item["required"] and key not in supplied for key, item in pending.items()):
                raise ValueError("Fill in every required answer.")
            answers = save_application_answers(jobs[key], pending, supplied)
            if TASK.get("automatic"):
                queued = key in application_queue(jobs)
                start_task(lambda: run_application(jobs, key, answers, skip_optional=True, queued=queued), BROWSER_WORKER)
            else:
                start_task(lambda: dict(BROWSER.fill(jobs[key], answers_for_job(jobs[key], answers), skip_optional=True),
                                        job_id=key), BROWSER_WORKER)
        elif route == "settings":
            datetime.strptime(data["time"], "%H:%M")
            old = work.read_json("settings", {})
            work.write_json("settings", dict(old, time=data["time"], enabled=bool(data["enabled"])))
        elif route == "shortlist":
            key = data["id"]
            job = work.read_json("jobs", {})[key]
            if not work.board_match(job) or not isinstance(data["saved"], bool):
                raise ValueError("Choose a matching job to save.")
            shortlist = set(work.read_json("shortlist", []))
            if data["saved"]:
                shortlist.add(key)
            else:
                shortlist.discard(key)
            work.write_json("shortlist", sorted(shortlist))
        elif route == "job":
            jobs = work.read_json("jobs", {})
            job = work.make_job(data["company"], data["title"], data["url"], data["description"])
            key = work.known_jobs(jobs).get(work.canonical_job(job["url"]), job["id"])
            job["id"] = key
            existing = jobs.get(key, {})
            jobs[key] = dict(existing, **job, archived=False,
                             first_seen=existing.get("first_seen", work.timestamp()))
            work.write_json("jobs", jobs)
            def assess():
                match = work.rank_batch(work.profile(), [job]).get(key, {})
                jobs[key].update(score=match.get("score", 0), reason=match.get("reason", ""))
                work.write_json("jobs", jobs)
                return ("Added to your matches." if work.board_match(jobs[key]) else
                        "Saved in the tracker. Only matches above 70% appear on the board.")
            if not existing.get("score") or existing.get("description") != job["description"]:
                start_task(assess, keep_pause=True)
        else:
            jobs = work.read_json("jobs", {})
            queue = application_queue(jobs)
            if route in {"remove-jobs", "restore-jobs", "queue-apply", "skip-queue"}:
                ids = list(dict.fromkeys(data["ids"]))
                if not ids or any(key not in jobs or not work.board_match(jobs[key]) for key in ids):
                    raise ValueError("Select at least one matching job.")
                if route in {"remove-jobs", "restore-jobs"}:
                    for key in ids:
                        if route == "remove-jobs":
                            jobs[key]["removed_at"] = work.timestamp()
                        else:
                            jobs[key].pop("removed_at", None)
                    work.write_json("jobs", jobs)
                    if route == "remove-jobs" and TASK.get("job_id") in ids:
                        TASK["questions"] = []
                    work.write_json("application-queue", [key for key in queue if key not in ids])
                    return
                if route == "skip-queue":
                    if TASK.get("job_id") in ids:
                        TASK.update(questions=[], attention="")
                    work.write_json("application-queue", [key for key in queue if key not in ids])
                    return
                if any(not eligible_to_apply(jobs[key]) for key in ids):
                    raise ValueError("Select active jobs that you have yet to apply for.")
                queue = list(dict.fromkeys(queue + ids))
                work.write_json("application-queue", queue)
                job = jobs[queue[0]]
            else:
                job = jobs[data["id"]]
            def application_step(operation, status=None):
                def run():
                    try:
                        result = operation()
                    except Exception as error:
                        work.record_application(job, "failed", str(error))
                        work.write_json("jobs", jobs)
                        raise
                    failures = result.get("failures", [])
                    if failures:
                        work.record_application(job, "failed", " ".join(failures))
                        work.write_json("jobs", jobs)
                    elif status and job.get("application_status") != status:
                        work.record_application(job, status)
                        work.write_json("jobs", jobs)
                    return result
                start_task(run, BROWSER_WORKER)
            if route in {"apply", "queue-apply"}:
                if not eligible_to_apply(job):
                    raise ValueError("Choose an active match that you have yet to apply for.")
                answers = work.sheet("answers")
                key = job["id"]
                start_task(lambda: run_application(jobs, key, answers, queued=key in queue), BROWSER_WORKER)
            elif route == "tailor":
                def tailor():
                    work.tailor(job)
                    return "LaTeX generated. Review the source and compile its PDF."
                start_task(tailor, keep_pause=True)
            elif route == "compile":
                start_task(lambda: work.compile_resume(job, data.get("source")), keep_pause=True)
            elif route == "open":
                if job.get("submitted"):
                    raise ValueError("This posting is recorded as applied. Update its status before reapplying.")
                application_step(lambda: BROWSER.start(job), "in progress")
            elif route == "fill":
                if BROWSER.job_id != job["id"]:
                    raise ValueError("Continue this application in Chrome before scanning its form.")
                answers = work.sheet("answers")
                if job.get("submitted"):
                    raise ValueError("This posting is already recorded as applied.")
                application_step(lambda: dict(BROWSER.fill(job, answers_for_job(job, answers)), job_id=job["id"]), "in progress")
            elif route == "submitted":
                if job.get("confirmed_submission_at"):
                    raise ValueError("This application was confirmed by the portal.")
                if TASK.get("job_id") == job["id"]:
                    TASK.update(questions=[], attention="")
                job.pop("submission_pending", None)
                work.record_application(job, "found" if job.get("submitted") else "applied")
                work.write_json("jobs", jobs)
                work.write_json("application-queue", [key for key in queue if key != job["id"]])
            elif route == "application-status":
                if job.get("confirmed_submission_at"):
                    raise ValueError("This application was confirmed by the portal.")
                if data["status"] not in {"found", "failed", "retry", "in progress", "applied"}:
                    raise ValueError("Choose an application status.")
                if job.get("submission_pending"):
                    if data["status"] not in {"retry", "applied"}:
                        raise ValueError("Verify the submission, then mark it applied or queue a retry.")
                    job.pop("submission_pending", None)
                work.record_application(job, data["status"], data.get("detail", "")[:1000])
                work.write_json("jobs", jobs)
                if data["status"] == "applied":
                    work.write_json("application-queue", [key for key in queue if key != job["id"]])
            else:
                raise ValueError("Unknown action.")


if __name__ == "__main__":
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Jobhunt is running at {ORIGIN}", flush=True)
    threading.Thread(target=scheduler, daemon=True).start()
    if "--open" in sys.argv:
        webbrowser.open(ORIGIN)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        WORKER.shutdown(wait=False, cancel_futures=True)
        BROWSER_WORKER.shutdown(wait=False, cancel_futures=True)
