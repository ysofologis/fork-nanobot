"""Session pack manager — organise sessions into topic-based packages.

Each pack is stored under ``{workspace}/sessions/packs/{session_name}/``
with a ``pack.json`` metadata file and numbered ``{index:02d}.md`` session
files.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loguru import logger

from nanobot.session.pack import SessionPackKey, parse_session_key

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PACKS_DIRNAME = "packs"
PACK_META_FILENAME = "pack.json"
SESSION_FILE_GLOB = "[0-9][0-9].md"
_SESSION_FILE_RE = re.compile(r"^(\d{2})\.md$")


# ---------------------------------------------------------------------------
# PackManager
# ---------------------------------------------------------------------------


class PackManager:
    """CRUD and search operations over session packs.

    Parameters
    ----------
    workspace:
        Root path of the nanobot workspace (containing ``sessions/``).
    """

    def __init__(self, workspace: Path) -> None:
        self._packs_root = workspace / "sessions" / PACKS_DIRNAME

    # -- public API ---------------------------------------------------------

    def resolve(self, key: str) -> dict[str, Any]:
        """Parse *key*, ensure its pack directory exists, return metadata.

        Creates ``pack.json`` on first access if the pack directory
        doesn't already hold one.
        """
        pack_key = parse_session_key(key)
        pack_dir = self._pack_dir(pack_key.session_name)
        pack_dir.mkdir(parents=True, exist_ok=True)

        meta_path = pack_dir / PACK_META_FILENAME
        if meta_path.exists():
            try:
                meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Corrupt pack.json for {!r}, re-creating", pack_key.session_name)
                meta = self._init_meta(pack_key)
                meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
        else:
            meta = self._init_meta(pack_key)
            meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        return meta

    def store(self, content: str, key: str) -> dict[str, Any]:
        """Store *content* as a session file and update pack metadata.

        Parameters
        ----------
        content:
            Condensed session text to persist.
        key:
            Full session key ``channel:name#NN``.

        Returns
        -------
        Updated pack metadata dict.
        """
        pack_key = parse_session_key(key)
        meta = self.resolve(key)
        pack_dir = self._pack_dir(pack_key.session_name)
        index = pack_key.index

        # Write the session file
        session_path = pack_dir / f"{index:02d}.md"
        session_path.write_text(content, encoding="utf-8")

        # Update metadata
        indices: list[int] = meta.get("indices", [])
        if index not in indices:
            indices.append(index)
            indices.sort()

        meta["indices"] = indices
        meta["session_count"] = len(indices)
        meta["updated"] = _utcnow()
        meta["channel"] = pack_key.channel or meta.get("channel", "")

        self._write_meta(pack_key.session_name, meta)
        return meta

    def get_pack(self, name: str) -> dict[str, Any] | None:
        """Return metadata for pack *name*, or ``None`` if it doesn't exist."""
        meta_path = self._pack_dir(name) / PACK_META_FILENAME
        if not meta_path.exists():
            return None
        try:
            meta: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        meta["session_name"] = name
        return meta

    def list_packs(self) -> list[dict[str, Any]]:
        """Return metadata dicts for every known pack."""
        results: list[dict[str, Any]] = []
        if not self._packs_root.is_dir():
            return results
        for child in sorted(self._packs_root.iterdir()):
            if not child.is_dir():
                continue
            meta = self.get_pack(child.name)
            if meta is not None:
                results.append(meta)
        return results

    def search(self, query: str) -> list[dict[str, Any]]:
        """Simple keyword search across pack metadata and session files.

        Returns a list of result dicts sorted by relevance (descending):

        .. code-block:: python
            {
                "session_name": str,
                "match_type": "title | keyword | summary | body",
                "snippet": str,
                "score": float,
            }
        """
        q = query.lower()
        results: list[dict[str, Any]] = []

        if not self._packs_root.is_dir():
            return results

        for child in sorted(self._packs_root.iterdir()):
            if not child.is_dir():
                continue
            name = child.name
            meta = self.get_pack(name)
            if meta is None:
                continue

            score = 0.0
            match_type: str | None = None
            snippet = ""

            # Title match (strongest)
            if q in name.lower():
                score = 0.9
                match_type = "title"
                snippet = name

            # Keywords match
            kw_list: list[str] = meta.get("keywords", []) or []
            for kw in kw_list:
                if q in kw.lower():
                    score = max(score, 0.7)
                    match_type = "keyword"
                    snippet = f"keyword: {kw}"

            # Summary match
            summary = meta.get("summary", "") or ""
            if q in summary.lower() and score < 0.6:
                score = 0.6
                match_type = "summary"
                # Extract a short snippet
                idx = summary.lower().find(q)
                start = max(0, idx - 40)
                end = min(len(summary), idx + len(q) + 60)
                snippet = ("..." if start > 0 else "") + summary[start:end] + ("..." if end < len(summary) else "")

            if score <= 0.0:
                # Fallback: scan session file bodies
                match_type, snippet, score = self._search_session_files(child, q)

            if match_type and score > 0:
                results.append({
                    "session_name": name,
                    "match_type": match_type,
                    "snippet": snippet,
                    "score": round(score, 2),
                })

        results.sort(key=lambda r: r["score"], reverse=True)
        return results

    def delete_pack(self, name: str) -> bool:
        """Remove a pack directory and all its files.

        Returns ``True`` if the pack existed and was removed.
        """
        pack_dir = self._pack_dir(name)
        if not pack_dir.is_dir():
            return False
        import shutil
        shutil.rmtree(pack_dir, ignore_errors=True)
        logger.info("Deleted session pack {!r}", name)
        return True

    # -- helpers ------------------------------------------------------------

    def _pack_dir(self, name: str) -> Path:
        return self._packs_root / name

    def _meta_path(self, name: str) -> Path:
        return self._pack_dir(name) / PACK_META_FILENAME

    def _write_meta(self, name: str, meta: dict[str, Any]) -> None:
        self._meta_path(name).write_text(
            json.dumps(meta, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    @staticmethod
    def _init_meta(pack_key: SessionPackKey) -> dict[str, Any]:
        now = _utcnow()
        return {
            "session_name": pack_key.session_name,
            "channel": pack_key.channel or "",
            "created": now,
            "updated": now,
            "session_count": 0,
            "indices": [],
            "summary": "",
            "keywords": [],
            "status": "active",
        }

    def _search_session_files(
        self,
        pack_dir: Path,
        q: str,
    ) -> tuple[str | None, str, float]:
        """Scan ``*.md`` files in *pack_dir* for *q*."""
        snippet = ""
        score = 0.0
        match_type: str | None = None

        for fpath in sorted(pack_dir.glob(SESSION_FILE_GLOB)):
            try:
                text = fpath.read_text(encoding="utf-8")
            except OSError:
                continue
            if q in text.lower():
                idx = text.lower().find(q)
                start = max(0, idx - 50)
                end = min(len(text), idx + len(q) + 80)
                snippet = ("..." if start > 0 else "") + text[start:end] + ("..." if end < len(text) else "")
                score = 0.3
                match_type = "body"
                break

        return match_type, snippet, score


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
