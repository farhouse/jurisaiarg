"""Reglas conservadoras para notas modificatorias de InfoLEG."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
import unicodedata
from dataclasses import dataclass


PARSER_VERSION = "legal-notes-v1"

NOTE_BLOCK = re.compile(
    r"\((?P<note>[^()]{0,900}?(?:sustituid[oa]|incorporad[oa]|derogad[oa]|modificad[oa])"
    r"\s+por\s+art(?:[íi]culo)?\.?\s*[^()]{0,900}?)\)",
    re.IGNORECASE,
)

CHANGE_PATTERN = re.compile(
    r"(?P<scope>Art[íi]culo|Inciso|P[aá]rrafo|Cap[íi]tulo|T[íi]tulo|Expresi[oó]n)"
    r"(?:\s+[\w°º.-]+){0,4}?\s+"
    r"(?P<action>sustituid[oa]|incorporad[oa]|derogad[oa]|modificad[oa])\s+"
    r"por\s+art(?:[íi]culo)?\.?\s*(?P<source_article>[0-9]+(?:\s*(?:bis|ter|qu[aá]ter))?)"
    r"\s*[°ºo]?(?:\s*,)?\s+(?:de\s+la|del)\s+"
    r"(?P<norm_type>Ley|Decreto(?:\s+DNU)?|Resoluci[oó]n(?:\s+General|\s+Conjunta)?|Disposici[oó]n)"
    r"\s*(?:N[°ºo]\.?\s*)?(?P<norm_number>[0-9][0-9.\-/]*)",
    re.IGNORECASE,
)

BO_DATE = re.compile(r"B\.?\s*O\.?\s*(?:del\s*)?(\d{1,2})/(\d{1,2})/(\d{2,4})", re.IGNORECASE)
EFFECT_DATE = re.compile(
    r"Vigencia\s*:[^.;)]*?(\d{1,2})/(\d{1,2})/(\d{2,4})", re.IGNORECASE
)


@dataclass(frozen=True)
class LegalChangeCandidate:
    change_type: str
    affected_scope: str
    amending_norm_type: str
    amending_norm_number: str
    amending_article_reference: str
    publication_date: dt.date | None
    effect_date: dt.date | None
    evidence_text: str

    @property
    def amending_reference_raw(self) -> str:
        return f"{self.amending_norm_type} {self.amending_norm_number}"


def _ascii(value: str) -> str:
    return unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode()


def normalize_norm_type(value: str) -> str:
    value = _ascii(value).lower()
    if value.startswith("decreto"):
        return "decreto"
    if value.startswith("ley"):
        return "ley"
    if value.startswith("resolucion"):
        return "resolucion"
    if value.startswith("disposicion"):
        return "disposicion"
    return re.sub(r"\s+", " ", value).strip()


def normalize_norm_number(value: str) -> str:
    return re.sub(r"[.\s]", "", value).upper()


def _date(match: re.Match | None) -> dt.date | None:
    if not match:
        return None
    day, month, year = (int(part) for part in match.groups())
    if year < 100:
        year += 2000 if year < 50 else 1900
    try:
        return dt.date(year, month, day)
    except ValueError:
        return None


def _scope(value: str) -> str:
    normalized = _ascii(value).lower()
    return {
        "articulo": "articulo",
        "inciso": "inciso",
        "parrafo": "parrafo",
        "capitulo": "capitulo",
        "titulo": "titulo",
        "expresion": "expresion",
    }.get(normalized, "desconocido")


def _action(value: str) -> str:
    normalized = _ascii(value).lower()
    if normalized.startswith("sustituid"):
        return "sustituye"
    if normalized.startswith("incorporad"):
        return "incorpora"
    if normalized.startswith("derogad"):
        return "deroga"
    return "modifica"


def extract_legal_changes(text: str) -> list[LegalChangeCandidate]:
    candidates = []
    seen = set()
    for note_match in NOTE_BLOCK.finditer(text or ""):
        evidence = " ".join(note_match.group(0).split())
        change_match = CHANGE_PATTERN.search(note_match.group("note"))
        if not change_match:
            continue
        publication = _date(BO_DATE.search(evidence))
        effect = _date(EFFECT_DATE.search(evidence))
        if effect is None and publication and re.search(
            r"Vigencia\s*:\s*a partir de (?:su|la) publicaci[oó]n", evidence, re.I
        ):
            effect = publication
        candidate = LegalChangeCandidate(
            change_type=_action(change_match.group("action")),
            affected_scope=_scope(change_match.group("scope")),
            amending_norm_type=" ".join(change_match.group("norm_type").split()),
            amending_norm_number=change_match.group("norm_number"),
            amending_article_reference=" ".join(change_match.group("source_article").split()),
            publication_date=publication,
            effect_date=effect,
            evidence_text=evidence,
        )
        key = (
            candidate.change_type, candidate.affected_scope,
            normalize_norm_type(candidate.amending_norm_type),
            normalize_norm_number(candidate.amending_norm_number),
            candidate.amending_article_reference, candidate.evidence_text,
        )
        if key not in seen:
            candidates.append(candidate)
            seen.add(key)
    return candidates


def fingerprint(affected_infoleg_id: int, affected_article_key: str,
                candidate: LegalChangeCandidate) -> str:
    identity = "|".join((
        str(affected_infoleg_id), affected_article_key, candidate.change_type,
        candidate.affected_scope, normalize_norm_type(candidate.amending_norm_type),
        normalize_norm_number(candidate.amending_norm_number),
        candidate.amending_article_reference, candidate.evidence_text,
    ))
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()
