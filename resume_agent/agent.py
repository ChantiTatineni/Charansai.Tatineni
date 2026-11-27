"""Resume agent: one running chat that tailors, critiques and remembers.

    python -m resume_agent.agent                 # live chat
    python -m resume_agent.agent --base my.docx  # set the base resume, then chat

Inside the chat, paste a job description, give feedback, state facts about
yourself, or lock decisions. Type /help for commands.
"""
from __future__ import annotations

import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from . import checks, docx_io
from .llm import Refused, ask
from .memory import HOME, Chat

RULES = (Path(__file__).parent / "MASTER_RULES.md").read_text()
OUT = HOME / "out"

PERSONA = """You are the owner's resume agent in a long-running chat. You are not a yes-man.

Every time the owner asks for something:
1. Do it. The deliverable is the tailored resume, not advice.
2. Check the request against everything earlier in this chat: facts, locked decisions,
   earlier requests and earlier resumes. If it contradicts something, say exactly what
   (cite the turn, F#, D# or R#) and ask which one wins. Until they answer, follow the
   newest instruction.
3. Argue when you think they are wrong. Say what you would do instead and why, in plain
   words, citing the job description or how recruiters read resumes. Keep it short and
   specific; no hedging, no flattery.
4. Say what improved since the last resume, what is still weak, and what you want to
   work on next.

Never invent employers, titles, dates, degrees, certifications, tools or metrics. When a
higher match needs something the facts do not show, name the exact fact you need and ask
for it. Anything the owner states about themselves goes in the fact ledger as true.

Tone with the owner: no lectures, no warnings about interviews or getting caught, no
"I can't do that". Always deliver the best resume the facts allow. For each missing
requirement, ask one short direct question ("Have you used dbt? Where, and for how long?")
so one answer from the owner closes the gap in the next draft.
"""


def system(chat: Chat) -> str:
    return f"{PERSONA}\n\n# MASTER RULES\n{RULES}\n\n# CHAT MEMORY\n{chat.brief()}"


# ---------------------------------------------------------------- schemas
class TurnPlan(BaseModel):
    intent: Literal["new_job", "revise_current", "feedback_only", "question"]
    job_description: str = Field(description="The full pasted job description, verbatim, if this message contains one; else empty.")
    company: str = Field(description="Company name from the job description, or empty.")
    title: str = Field(description="Job title from the job description, or empty.")
    facts_stated: list[str] = Field(description="New things the owner explicitly says are true about their own background in THIS message.")
    decisions_stated: list[str] = Field(description="New standing preferences or decisions the owner sets in THIS message (e.g. 'keep it to one page').")
    conflicts: list[str] = Field(description="Where this message contradicts earlier turns, facts (F#), decisions (D#) or resumes (R#). Cite them.")
    revise_instructions: str = Field(description="For revise_current: exactly what to change in the next draft.")
    reply: str = Field(description="Your direct reply: what you will do, where you disagree and what you'd do instead.")


class Requirement(BaseModel):
    requirement: str
    tier: Literal[1, 2, 3]
    supported: bool
    evidence: list[int] = Field(description="Paragraph indices in the base resume that prove it.")
    note: str


class Analysis(BaseModel):
    company: str
    title: str
    tier1: list[str]
    tier2: list[str]
    tier3: list[str]
    requirements: list[Requirement]
    gaps: list[str] = Field(description="Requirements the base resume and fact ledger do not support.")
    questions: list[str] = Field(description="Specific questions whose answers would close a gap honestly.")


class Edit(BaseModel):
    i: int = Field(description="Paragraph index in the base resume.")
    text: str = Field(description="Full replacement text for that paragraph. No bullet characters.")


class Draft(BaseModel):
    edits: list[Edit]
    reorders: list[list[int]] = Field(description="Groups of bullet indices of ONE role, in the new order (strongest first).")
    deletions: list[int] = Field(description="Paragraph indices to remove (weak, irrelevant bullets only).")
    notes: str


class Review(BaseModel):
    passed: bool
    issues: list[str]


class Score(BaseModel):
    required_skills: int = Field(description="0-30")
    experience: int = Field(description="0-25")
    responsibilities: int = Field(description="0-20")
    terminology: int = Field(description="0-15")
    ats_structure: int = Field(description="0-10")


class Critique(BaseModel):
    ats: Review
    hiring_manager: Review
    editor: Review
    unsupported_claims: list[str] = Field(description="Claims in the draft not backed by the base resume or fact ledger. Quote them.")
    must_fix: list[str] = Field(description="Concrete instructions for the next draft. Empty if it is ready.")
    score: Score
    score_ceiling_note: str = Field(description="What would raise the score honestly, and which facts you need from the owner.")
    pushback: str = Field(description="Where you think the owner's instructions or decisions are hurting this resume, and what you'd do instead. Empty if none.")
    next_focus: list[str]
    ready: bool


# ---------------------------------------------------------------- pipeline
YEAR = re.compile(r"\b(19|20)\d{2}\b|\bpresent\b", re.I)
NUM = re.compile(r"\d[\d,.]*\s*(%|\+|k|m|x)?", re.I)


def _guard(base_rows: dict[int, str], edits: list[Edit], allowed_text: str) -> tuple[dict[int, str], list[str]]:
    """Drop edits that change dates or add numbers the sources do not contain."""
    ok, flags = {}, []
    for e in edits:
        old = base_rows.get(e.i)
        if old is None:
            flags.append(f"Edit to unknown paragraph {e.i} ignored.")
            continue
        if sorted(m.group(0).lower() for m in YEAR.finditer(old)) != sorted(m.group(0).lower() for m in YEAR.finditer(e.text)):
            flags.append(f"Rejected edit to [{e.i}]: it changed a date. Dates are never edited.")
            continue
        bad = [m.group(0).strip() for m in NUM.finditer(e.text)
               if len(m.group(0).strip()) > 1 and m.group(0).strip() not in allowed_text]
        if bad:
            flags.append(f"Kept the original [{e.i}]: the rewrite added numbers not in the resume or facts ({', '.join(bad)}). "
                         "State them as a fact if they are real.")
            continue
        ok[e.i] = e.text.lstrip("•-–* ").strip()
    return ok, flags


def _render(rows: list[dict], edits: dict[int, str], draft: Draft) -> list[dict]:
    by_i = {r["i"]: dict(r, text=edits.get(r["i"], r["text"])) for r in rows}
    order = [r["i"] for r in rows]
    for group in draft.reorders:
        slots = sorted(i for i in group if i in by_i)
        valid = [i for i in group if i in by_i]
        pos = {s: v for s, v in zip(slots, valid)}
        order = [pos.get(i, i) for i in order]
    return [by_i[i] for i in order if i not in set(draft.deletions)]


def _safe(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "", name.title()) or "Role"


def tailor(chat: Chat, jd: str, extra: str = "", say=print) -> dict:
    base = chat.d["base_resume"]
    if not base:
        raise RuntimeError("No base resume yet. Run /base path/to/resume.docx")
    rows = docx_io.extract(base)
    base_text = docx_io.as_numbered_text(rows)
    base_rows = {r["i"]: r["text"] for r in rows}
    sources = "\n".join(base_rows.values()) + "\n" + "\n".join(chat.facts)
    sys_prompt = system(chat)

    say("· analyzing the job description against your resume and facts…")
    a = ask(sys_prompt, f"# BASE RESUME (paragraph index in brackets)\n{base_text}\n\n# JOB DESCRIPTION\n{jd}\n\n"
                        "Analyze the job and map every requirement to evidence.", Analysis)

    brief = (f"# BASE RESUME (paragraph index in brackets)\n{base_text}\n\n# JOB DESCRIPTION\n{jd}\n\n"
             f"# ANALYSIS\n{a.model_dump_json(indent=1)}\n\n"
             f"# OWNER'S INSTRUCTIONS FOR THIS DRAFT\n{extra or '(none beyond the decisions in memory)'}\n\n"
             "Write the tailored resume as paragraph edits on the base resume. Rewrite the summary and skills, "
             "rewrite and reorder bullets. Never edit employer, title, date, location or education lines except to "
             "reword nothing. Use only facts from the base resume and the fact ledger.")
    feedback = ""
    critique: Critique | None = None
    for attempt in range(3):
        say(f"· writing draft {attempt + 1}…")
        d = ask(sys_prompt, brief + feedback, Draft)
        edits, flags = _guard(base_rows, d.edits, sources)
        bullet_ids = {r["i"] for r in rows if r["bullet"]}
        d.deletions = [i for i in d.deletions if i in bullet_ids]
        d.reorders = [[i for i in g if i in bullet_ids] for g in d.reorders]
        draft_rows = _render(rows, edits, d)
        lines = [r["text"] for r in draft_rows]
        bullets = [r["text"] for r in draft_rows if r["bullet"]]
        hard = checks.report(lines, bullets, {"tier1": a.tier1, "tier2": a.tier2, "tier3": a.tier3})
        hard["guard_flags"] = flags

        say(f"· critiquing draft {attempt + 1} (ATS, hiring manager, editor)…")
        critique = ask(sys_prompt,
                       f"# JOB DESCRIPTION\n{jd}\n\n# ANALYSIS\n{a.model_dump_json(indent=1)}\n\n"
                       f"# BASE RESUME\n{base_text}\n\n# DRAFT\n" + "\n".join(lines) +
                       f"\n\n# AUTOMATED CHECKS\n{hard}\n\n# OWNER'S INSTRUCTIONS FOR THIS DRAFT\n{extra or '(none)'}\n\n"
                       "Review the draft hard. Any slop hit, filler ending, unsupported claim or guard flag means it is not ready.",
                       Critique)
        clean = not hard["slop"] and not flags and not critique.unsupported_claims
        if critique.ready and clean:
            break
        feedback = ("\n\n# CRITIC'S MUST-FIX LIST FOR THE NEXT DRAFT\n" + "\n".join(f"- {x}" for x in critique.must_fix)
                    + "\n- " + "\n- ".join(f"Slop: '{h['phrase']}' in: {h['text']}" for h in hard["slop"])
                    + "\n- " + "\n- ".join(flags + critique.unsupported_claims))

    # write files
    OUT.mkdir(parents=True, exist_ok=True)
    stem = f"Resume_{_safe(a.company)}_{_safe(a.title)}"
    n = len(chat.d["runs"]) + 1
    docx_path = OUT / f"{stem}_R{n}.docx"
    docx_io.apply_edits(base, docx_path, edits, d.reorders, set(d.deletions))
    pdf_path = None
    try:
        pdf_path = docx_io.to_pdf(docx_path)
    except Exception as exc:  # LibreOffice problems should not lose the .docx
        say(f"  (PDF conversion failed: {exc})")

    s = critique.score
    total = s.required_skills + s.experience + s.responsibilities + s.terminology + s.ats_structure
    prev = chat.last_run
    report = _report(a, critique, hard, total, prev, d.notes)
    report_path = OUT / f"{stem}_R{n}_report.md"
    report_path.write_text(report)

    chat.add_run({
        "company": a.company, "title": a.title, "score": total, "jd": jd, "extra": extra,
        "gaps": a.gaps, "questions": a.questions, "files": [str(docx_path), str(pdf_path or ""), str(report_path)],
        "pushback": critique.pushback, "next_focus": critique.next_focus,
    })
    chat.d["next_focus"] = critique.next_focus
    return {"report": report, "docx": docx_path, "pdf": pdf_path, "report_path": report_path, "score": total}


def _report(a: Analysis, c: Critique, hard: dict, total: int, prev: dict | None, notes: str) -> str:
    s = c.score
    cov = hard["coverage"]
    out = [
        f"# {a.title} at {a.company}",
        "",
        f"**Estimated alignment: {total}/100** (rubric estimate, not an official ATS score)",
        f"Required skills {s.required_skills}/30 · Experience {s.experience}/25 · Responsibilities {s.responsibilities}/20 · "
        f"Terminology {s.terminology}/15 · ATS structure {s.ats_structure}/10",
    ]
    if prev:
        delta = total - (prev.get("score") or 0)
        out.append(f"Compared with R{prev['n']} ({prev.get('company')} / {prev.get('title')}): {delta:+d}")
    out += ["", f"Keyword coverage: Tier 1 {cov['tier1']['pct']}% · Tier 2 {cov['tier2']['pct']}% · Tier 3 {cov['tier3']['pct']}%"]
    if cov["tier1"]["missing"]:
        out.append(f"Tier 1 still missing: {', '.join(cov['tier1']['missing'])}")
    out += ["", "## Matched requirements"] + [f"- {r.requirement}" for r in a.requirements if r.supported]
    out += ["", "## Gaps I would not fake"] + ([f"- {g}" for g in a.gaps] or ["- none"])
    out += ["", "## Questions for you (answers can raise the score)"] + ([f"- {q}" for q in a.questions] or ["- none"])
    out += ["", "## What would raise it", c.score_ceiling_note]
    if c.pushback:
        out += ["", "## Where I disagree with you", c.pushback]
    for name, rv in (("ATS review", c.ats), ("Hiring-manager review", c.hiring_manager), ("Editor review", c.editor)):
        out += ["", f"## {name}: {'pass' if rv.passed else 'needs work'}"] + [f"- {i}" for i in rv.issues]
    if hard["slop"] or hard["guard_flags"]:
        out += ["", "## Leftover automated flags"] + [f"- {h['phrase']}: {h['text']}" for h in hard["slop"]] + [f"- {f}" for f in hard["guard_flags"]]
    out += ["", "## Writer's notes", notes, "", "## Next focus"] + [f"- {x}" for x in c.next_focus]
    return "\n".join(out)


# ---------------------------------------------------------------- chat loop
HELP = """Commands:
  /base PATH        set the base resume (.docx)
  /fact TEXT        add a fact about yourself
  /lock TEXT        lock a decision the agent must follow
  /unlock N         drop decision D#N
  /status           show facts, decisions, resumes and next focus
  /quit             leave (everything is saved)
Anything else is a message: paste a job description, give feedback, ask a question."""


def handle(chat: Chat, msg: str, say=print) -> None:
    cmd, _, arg = msg.partition(" ")
    if cmd == "/help":
        say(HELP); return
    if cmd == "/base":
        src = Path(arg.strip()).expanduser()
        if src.suffix.lower() != ".docx" or not src.exists():
            say("Give a path to an existing .docx file."); return
        HOME.mkdir(parents=True, exist_ok=True)
        dst = HOME / "base.docx"
        shutil.copy(src, dst)
        chat.d["base_resume"] = str(dst)
        chat.turn("owner", f"(set base resume: {src.name})")
        say(f"Base resume set ({len(docx_io.extract(dst))} paragraphs)."); return
    if cmd == "/fact":
        chat.add_fact(arg.strip()); chat.turn("owner", f"(fact) {arg}"); say("Noted as fact."); return
    if cmd == "/lock":
        chat.add_decision(arg.strip()); chat.turn("owner", f"(decision) {arg}"); say("Locked."); return
    if cmd == "/unlock":
        chat.drop_decision(int(arg)); chat.turn("owner", f"(dropped decision D{arg})"); say("Dropped."); return
    if cmd == "/status":
        say(chat.brief(max_turns=10)); return

    chat.turn("owner", msg)
    plan = ask(system(chat), f"# NEW MESSAGE FROM THE OWNER\n{msg}\n\nWork out what they want, check it against the chat memory, and reply.", TurnPlan, effort="medium")
    for f in plan.facts_stated:
        chat.add_fact(f)
    for x in plan.decisions_stated:
        chat.add_decision(x)
    say("")
    say(plan.reply)
    if plan.conflicts:
        say("\nConflicts with earlier in this chat:\n" + "\n".join(f"  - {c}" for c in plan.conflicts))
    if plan.facts_stated or plan.decisions_stated:
        say("\nRecorded: " + "; ".join([f"fact: {f}" for f in plan.facts_stated] + [f"decision: {x}" for x in plan.decisions_stated]))
    reply = plan.reply

    jd, extra = None, ""
    if plan.intent == "new_job" and plan.job_description.strip():
        jd = plan.job_description
    elif plan.intent == "revise_current" and chat.last_run:
        jd, extra = chat.last_run["jd"], plan.revise_instructions
    elif plan.intent == "revise_current":
        say("\nThere is no resume to revise yet. Paste a job description first.")

    if jd:
        res = tailor(chat, jd, extra, say)
        say("\n" + res["report"])
        say(f"\nFiles:\n  {res['docx']}\n  {res['pdf'] or '(no PDF: LibreOffice not found)'}\n  {res['report_path']}")
        reply += f"\n\n[Produced R{len(chat.d['runs'])}: score {res['score']}/100]"
    chat.turn("agent", reply)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", help="base resume .docx")
    ap.add_argument("-m", "--message", help="send one message and exit (no live chat)")
    args = ap.parse_args()
    chat = Chat()
    try:
        if args.base:
            handle(chat, f"/base {args.base}")
            chat.save()
        if args.message:
            handle(chat, args.message)
            return
        print("Resume agent. Paste a job description or type /help. For a multi-line paste (a job description), type <<< on its own line, paste, then >>> on its own line.")
        while True:
            try:
                first = input("\nyou> ").rstrip()
            except EOFError:
                break
            if first in ("/quit", "/exit"):
                break
            if not first:
                continue
            lines = [first]
            if first == "<<<":
                # multi-line paste: read until a line with only >>>
                lines = []
                while True:
                    try:
                        nxt = input()
                    except EOFError:
                        break
                    if nxt.strip() == ">>>":
                        break
                    lines.append(nxt)
            try:
                handle(chat, "\n".join(lines))
            except Refused as exc:
                print(f"\nClaude declined: {exc}")
            except Exception as exc:
                print(f"\nError: {exc}")
            finally:
                chat.save()
    finally:
        chat.save()


if __name__ == "__main__":
    sys.exit(main())
