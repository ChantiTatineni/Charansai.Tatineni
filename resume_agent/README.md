# Resume agent

A resume agent that runs as one continuous chat. It tailors your Word resume to each job description, critiques its own drafts and argues with you. It remembers every request, fact, decision and resume from earlier in the chat.

## Setup

```bash
pip install -r resume_agent/requirements.txt
export ANTHROPIC_API_KEY=...        # or: ant auth login
```

LibreOffice (`soffice`) is used to make the PDF. Without it you still get the .docx.

## Run

```bash
python -m resume_agent --base path/to/your_resume.docx   # first time
python -m resume_agent                                   # later: picks up the same chat
```

Paste a job description between `<<<` and `>>>` lines. After that, just talk to it:

- "Put Tableau before Python in skills": it does that, and tells you if it thinks you're wrong.
- "I used Kafka at Acme for about a year": recorded as a fact and used from then on.
- "Always keep it to one page": recorded as a locked decision.
- "Make the summary shorter": it revises the current resume and compares the result with the last one.

Commands: `/base`, `/fact`, `/lock`, `/unlock N`, `/status`, `/help`, `/quit`.

## What each request goes through

1. **Check against memory.** Your message is compared with every earlier turn, fact (F#), locked decision (D#) and resume (R#). Conflicts are named and cited. New facts and decisions are saved.
2. **Analyze.** Job requirements are sorted into Tiers 1, 2 and 3, and each one is mapped to a line of your resume or to a gap.
3. **Write.** The agent edits paragraphs of your own .docx, so fonts, bold labels, bullets and spacing stay the same.
4. **Critique.** Three reviews run: ATS, hiring manager and editor. Code checks run too: a banned-phrase and filler-ending list, repeated opening verbs, and Tier 1, 2 and 3 keyword coverage.
5. **Revise.** It rewrites up to 2 more times until the critic passes the draft.
6. **Output.** You get `Resume_<Company>_<Title>_R#.docx`, a `.pdf` and a `_report.md` with the score breakdown, gaps, questions, pushback and next focus.

Everything is saved in `resume_agent/workspace/`, which is git-ignored.

## Hard limits (enforced in code, not just the prompt)

- Edits that change a date are rejected.
- A rewrite that adds a number or metric not found in your resume or facts is dropped, and the original line is kept. Tell the agent the number as a fact and it can be used.
- Only bullets can be deleted or reordered. Header lines (employer, title, dates) cannot.

The agent works only from what you've told it. If a job needs something you have, say so in the chat and it becomes a fact.

## Settings

- Change behaviour by editing `MASTER_RULES.md`.
- `RESUME_AGENT_MODEL` sets the model (default `claude-opus-5-5`).
- `RESUME_AGENT_EFFORT` sets the effort level (default `high`).
