"""Promptly Guide's admin console in Atlas.AI (promptly-guide N1, option A)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class GuideConsoleIn(BaseModel):
    """The console document, in the shape of Guide's file console
    (promptly-guide ``deploy/console.example.json``), and why it changed."""

    model_config = ConfigDict(extra="forbid")

    console: dict[str, Any]
    note: str | None = Field(default=None, max_length=500)


class GuideConsoleFileOut(BaseModel):
    """One file an admin can download."""

    name: str
    # The group a Mac profile puts a Mac in; None for the profile for every Mac and
    # for the Windows files.
    group: str | None = None


class GuideConsoleOut(BaseModel):
    """The organisation's console settings and the files made from them."""

    # None until an admin saves settings for the first time.
    console: dict[str, Any] | None
    # 0 until then; one more for every save that changed something.
    revision: int
    updated_at: datetime | None
    updated_by: str | None
    # The configuration profiles for Jamf or Intune: one for every Mac, one per group.
    profiles: list[GuideConsoleFileOut]
    # The ADMX template, its en-US ADML and test .reg files, for Windows PCs.
    windows_files: list[GuideConsoleFileOut]
    # What the Windows files leave out, and why (a Mac path, a Mac bundle id).
    windows_notes: list[str]
    # Why no files are offered: the saved settings are ones this version of Guide's
    # console refuses, in its own words. None when the files are offered.
    problem: str | None = None
