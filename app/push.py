"""Pushing a set of library tracks onto a Creative Tonie."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import audio, config, library, tonies

Progress = Callable[[str], None]


def _noop(_: str) -> None:
    return None


def client_from_settings() -> tonies.TonieCloud:
    """Env vars win; the UI-stored credentials are the fallback."""
    from . import db

    username = config.TONIES_USERNAME or db.get_setting("tonies_username")
    password = config.TONIES_PASSWORD or db.get_setting("tonies_password")
    if not username or not password:
        raise tonies.AuthError(
            "No myTonies credentials configured. Set TONIES_USERNAME and "
            "TONIES_PASSWORD, or save them on the Settings tab."
        )
    return tonies.TonieCloud(username, password)


def resolve_tracks(slug: str, names: list[str] | None, group_index: int | None) -> list[dict[str, Any]]:
    manifest = library.get(slug)
    if not manifest:
        raise RuntimeError(f"No collection named {slug}.")
    tracks = manifest["tracks"]

    if names:
        by_name = {t["name"]: t for t in tracks}
        missing = [n for n in names if n not in by_name]
        if missing:
            raise RuntimeError(f"Not in this collection: {', '.join(missing)}")
        return [by_name[n] for n in names]

    if group_index:
        groups = library.plan_groups(tracks)
        if group_index < 1 or group_index > len(groups):
            raise RuntimeError(f"This collection only has {len(groups)} group(s).")
        by_name = {t["name"]: t for t in tracks}
        # Match on filename only: two chapters can share a title.
        return [by_name[str(t.path)] for t in groups[group_index - 1].tracks
                if str(t.path) in by_name]

    return tracks


def push(
    slug: str,
    household_id: str,
    tonie_id: str,
    names: list[str] | None = None,
    group_index: int | None = None,
    replace: bool = True,
    progress: Progress = _noop,
) -> dict[str, Any]:
    tracks = resolve_tracks(slug, names, group_index)
    if not tracks:
        raise RuntimeError("Nothing selected to push.")

    total = sum(t.get("seconds", 0) for t in tracks)
    limit = config.TONIE_LIMIT_SECONDS
    if total > limit:
        raise RuntimeError(
            f"That selection is {audio.human_duration(total)}, over the "
            f"{audio.human_duration(limit)} a Creative Tonie holds. "
            f"Split it or push one group at a time."
        )

    client = client_from_settings()
    try:
        progress("Signing in to myTonies")
        client.check_login()

        if replace:
            progress("Clearing the Tonie")
            client.clear_tonie(household_id, tonie_id)

        uploaded = []
        for position, track in enumerate(tracks, start=1):
            path = library.track_path(slug, track["name"])
            label = track.get("title") or Path(track["name"]).stem
            progress(f"Uploading {position}/{len(tracks)}: {label}")
            file_id = client.upload_file(path)
            client.add_chapter(household_id, tonie_id, label, file_id)
            uploaded.append({"title": label, "file": file_id})

        progress("Confirming")
        state = client.get_tonie(household_id, tonie_id)
        return {
            "tonie": state.get("name", tonie_id),
            "chapters": len(state.get("chapters", [])),
            "uploaded": uploaded,
            "seconds": round(total, 1),
            "duration": audio.human_duration(total),
        }
    finally:
        client.close()
