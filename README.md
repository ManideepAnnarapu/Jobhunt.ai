# Jobhunt

Jobhunt is a local job search and application dashboard for macOS. Its server listens only on loopback. Companies and answer data live in editable CSV files in `.local/`. Your base files live in `BASE CV/`, and tailored résumés live in `Resumes/<company>/<role>-<id>/`.

## Start

Open `launch.command` in Finder or run `./launch.command` in Terminal. The launcher creates a Python virtual environment and installs the required packages on first use. Open http://127.0.0.1:8765 if the dashboard does not open automatically.

- Use Python 3.9+, Google Chrome, and a signed-in Codex CLI. The launcher looks for Codex on PATH and in the VS Code extension. Set `JOBHUNT_CODEX` for another executable.
- Jobhunt checks for `pdflatex` on PATH, in `/Library/TeX/texbin`, and in the workspace’s `.local/tex/TinyTeX` installation. Use a TeX distribution such as TinyTeX, BasicTeX, or MacTeX if none is available. Your template may require extra packages.
- The PDF and job text needed for matching and tailoring are sent through your configured Codex provider. The answer library stays in local CSV storage and is used by the Chrome form assistant.

## Find roles

Upload a PDF with selectable text under **Base résumé**. The tracked company list comes from `company-portals.csv`. Each original link appears in the dashboard. Feed mappings live in `portal-sources.json`. Jobhunt scans Greenhouse, Lever, Ashby, Amazon Jobs, R1’s Workday board, and public Apple and Google search pages. Recent search windows cover 100 results each for Amazon and AWS, 80 for R1, five pages for Apple, and three for Google. Meta remains available for manually adding individual job links. The Web3 Spectral domain is tracked separately from the physical-engineering company’s careers site.

The daily schedule defaults to 8:00 a.m. Eastern. On the dashboard, **Scan board** runs one source and **Retry failed scans** rechecks sources with fetch or ranking failures. A scan saves discoveries before ranking. It reuses existing scores when the posting and base résumé are unchanged and preserves application history on refresh. The job board shows matches strictly above 70%, including jobs added manually after scoring. Filter by company, location, and application status or sort by newest discovery. The tracker CSV includes all findings. **Update status** records a note and timestamp for each change.

### Run from login

To keep scheduled scans running independently of Terminal, stop any foreground instance with Ctrl+C and run `.venv/bin/python login_service.py install` from the project folder. This starts a per-user macOS service and starts it again at future logins. Open the dashboard and enable the desired time. If the Mac is asleep at that time, the scan runs after wake while the app is active. Remove the service using `.venv/bin/python login_service.py remove`.

## Tailor and compile

Upload the UTF-8 LaTeX document that generated your base PDF. Place a unique marker pair around each editable plain-text line. Jobhunt automatically marks compatible plain bullets for an unmarked template using the `resume` document class. For this class, upload the original `resume.cls` when your project has a customized version. Jobhunt includes a compatible fallback that initializes its header after the hyperlink package.

```latex
\item{
% JOBHUNT BEGIN experience-1
The existing accomplishment goes here.
% JOBHUNT END experience-1
}
```

Use **Tailor résumé**, then **Edit LaTeX / compile** to review the generated source and create a PDF. The compiler rejects a PDF with fewer extracted words than the base, a different page count, or different page dimensions. Text can reflow within a page, so review the PDF before applying. The dashboard provides both source and PDF downloads.

## Prepare the application

Choose **Auto-apply** to review the role, create an audited tailored PDF, and open the posting in Jobhunt’s Chrome profile. On a supported Greenhouse, Ashby, or Lever form, Jobhunt fills saved answers, attaches the PDF, and submits when it can verify the destination and required fields. **Auto-apply selected** processes a saved queue until a job needs your input.

When the dashboard asks a question, enter the answer and choose **Save and continue**. Jobhunt saves it for future questions with the same normalized wording. Logins, verification, required attestations, unsupported forms, and unclear submission results may require action in Chrome. If Jobhunt attempted submission but could not confirm it, verify the result in Chrome before allowing a retry. Use **Mark applied** for a submission you confirm yourself.

The **Fit review** action shows the role requirements tied to exact résumé evidence, gaps, and any unsupported claims found during the independent audit. Fit gaps are visible in the report. After you choose Auto-apply, Jobhunt continues to the form using the experience documented in your résumé. Use **Remove from board** or **Remove selected** to hide postings across future scans. Restore them from the **Removed** status filter.

The CSVs can be downloaded from the dashboard or edited on disk while the app is idle. Preserve headers `company,portal` and `question,answer`. Saved answers match normalized question text and are reused across portals. Review the filled values for role-specific answers such as salary and location.

For free-text application questions about your experience or interest in the role, choose **Draft from résumé and job** in the question popup. Review the draft and its résumé evidence, and edit it before choosing **Save and continue** or **Save and fill**. Narrative answers are saved per role. Reusable answers remain in the Answer library.

Choose **Save** on a job to keep it in your **Shortlist**. Jobhunt remembers the board filters in your browser. Press `/` on the job board to focus search, or use **Reset** to clear the filters. Search the Answer Library by question or answer text.
