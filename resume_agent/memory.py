"""One running chat. Every request, reply, fact, decision and tailored resume
is appended here, and every new request is checked against all of it."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

HOME = Path(__file__).parent / "workspace"
CHAT = HOME / "chat.json"


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")


class Chat:
    def __init__(self, path: Path = CHAT):
        self.path = path
        path.parent.mkdir(parents=True, exist_ok=True)
        self.d = json.loads(path.read_text()) if path.exists() else {
            "base_resume": None,
            "facts": [],        # things the owner said are true about them
            "decisions": [],    # things the owner decided; the agent follows them and may argue
            "turns": [],        # every request and reply, in order
            "runs": [],         # every tailored resume produced
            "next_focus": [],   # what the agent wants to work on next
        }

    def save(self) -> None:
        self.path.write_text(json.dumps(self.d, indent=2))

    # ---- writes -------------------------------------------------------------
    def turn(self, who: str, text: str) -> None:
        self.d["turns"].append({"n": len(self.d["turns"]) + 1, "who": who, "at": _now(), "text": text})

    def add_fact(self, text: str) -> None:
        if text and text not in [f["text"] for f in self.d["facts"]]:
            self.d["facts"].append({"n": len(self.d["facts"]) + 1, "at": _now(), "text": text})

    def add_decision(self, text: str) -> None:
        if text and text not in [x["text"] for x in self.d["decisions"] if x["active"]]:
            self.d["decisions"].append({"n": len(self.d["decisions"]) + 1, "at": _now(), "text": text, "active": True})

    def drop_decision(self, n: int) -> None:
        for x in self.d["decisions"]:
            if x["n"] == n:
                x["active"] = False

    def add_run(self, run: dict) -> None:
        run = {"n": len(self.d["runs"]) + 1, "at": _now(), **run}
        self.d["runs"].append(run)

    # ---- reads --------------------------------------------------------------
    @property
    def facts(self) -> list[str]:
        return [f["text"] for f in self.d["facts"]]

    @property
    def decisions(self) -> list[dict]:
        return [x for x in self.d["decisions"] if x["active"]]

    @property
    def last_run(self) -> dict | None:
        return self.d["runs"][-1] if self.d["runs"] else None

    def brief(self, max_turns: int = 60) -> str:
        """Everything the agent must remember, as text for the model."""
        d = self.d
        parts = ["## Facts the owner has stated (treat as true)"]
        parts += [f"F{f['n']}. {f['text']}" for f in d["facts"]] or ["(none yet)"]
        parts += ["", "## Decisions the owner has locked in (follow them; argue if you disagree)"]
        parts += [f"D{x['n']}. {x['text']}" for x in self.decisions] or ["(none yet)"]
        parts += ["", "## Resumes produced so far in this chat"]
        for r in d["runs"]:
            parts.append(
                f"R{r['n']} ({r['at']}): {r.get('company')} / {r.get('title')} — score {r.get('score')}; "
                f"gaps: {', '.join(r.get('gaps', [])[:6]) or 'none'}; "
                f"open questions: {' | '.join(r.get('questions', [])[:4]) or 'none'}"
            )
        if not d["runs"]:
            parts.append("(none yet)")
        parts += ["", "## What the agent said it would focus on next"]
        parts += [f"- {x}" for x in d["next_focus"]] or ["(nothing yet)"]
        parts += ["", f"## Conversation so far (last {max_turns} turns)"]
        for t in d["turns"][-max_turns:]:
            body = t["text"] if len(t["text"]) < 1500 else t["text"][:1500] + " …[truncated]"
            parts.append(f"[{t['n']}] {t['who'].upper()}: {body}")
        return "\n".join(parts)
