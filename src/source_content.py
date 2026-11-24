"""Select literal Markdown sections; never generate or approve operations."""

from __future__ import annotations

import re


def document_sections(body: str) -> dict[str, str]:
    headings = list(re.finditer(r"^## ([^\n]+)$", body, re.MULTILINE))
    return {
        match.group(1).strip(): body[
            match.start() : headings[index + 1].start() if index + 1 < len(headings) else len(body)
        ].strip()
        for index, match in enumerate(headings)
    }


def employee_sections(body: str) -> list[str]:
    """An editorial heading identifies employee-facing actions, never model guesses."""
    return [
        title for title in document_sections(body) if title.partition(" / ")[0] == "Employee steps"
    ]


def employee_procedure(body: str, section: str) -> str:
    sections = document_sections(body)
    matches = [
        content
        for title, content in sections.items()
        if title in employee_sections(body)
        and section.casefold() in {title.casefold(), title.partition(" / ")[0].casefold()}
    ]
    if len(matches) != 1:
        raise ValueError(
            "not_employee_section: choose an employee_sections heading; other headings are for investigation"
        )
    return matches[0]


def read_section(body: str, section: str) -> str:
    """Read any existing section, without authorizing employee operations."""
    matches = [
        content
        for title, content in document_sections(body).items()
        if section.casefold() in {title.casefold(), title.partition(" / ")[0].casefold()}
    ]
    if len(matches) != 1:
        raise ValueError("unknown_or_ambiguous_section: choose an exact heading from sections")
    return matches[0]
