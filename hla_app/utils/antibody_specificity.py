"""Вспомогательные правила для подписей и фильтрации HLA-специфичностей.

Здесь собраны небольшие pure-функции, которые используются при построении
динамики антител: формирование high/low-resolution labels, фильтр по
A/B/DRB1 и детерминированный ключ цвета для одной и той же подписи серии.
Если логика подписей меняется, смотреть прежде всего этот модуль.
"""

from __future__ import annotations

from hashlib import sha1


def _clean(value: str | None) -> str:
    return (value or "").strip()


def build_high_resolution_label(
    gene: str | None,
    allele_group: str | None,
    allele: str | None,
) -> str:
    gene_text = _clean(gene)
    group_text = _clean(allele_group)
    allele_text = _clean(allele)

    if not gene_text:
        return ""

    if not group_text:
        return gene_text

    if not allele_text:
        return f"{gene_text}*{group_text}"

    return f"{gene_text}*{group_text}:{allele_text}"


def build_low_resolution_label(
    gene: str | None,
    allele_group: str | None,
) -> str:
    gene_text = _clean(gene)
    group_text = _clean(allele_group)

    if not gene_text:
        return ""

    if not group_text:
        return gene_text

    return f"{gene_text}*{group_text}"


def build_series_label(
    *,
    gene: str | None,
    allele_group: str | None,
    allele: str | None,
    resolution: str,
) -> str:
    if resolution == "high":
        return build_high_resolution_label(gene, allele_group, allele)

    return build_low_resolution_label(gene, allele_group)


def passes_ab_drb1_filter(
    *,
    hla_class: int,
    gene: str | None,
    enabled: bool,
) -> bool:
    if not enabled:
        return True

    gene_text = _clean(gene).upper()

    if hla_class == 1:
        return gene_text in {"A", "B"}

    if hla_class == 2:
        return gene_text == "DRB1"

    return False


def stable_color_key(label: str) -> int:
    # Один и тот же label должен получать один и тот же цвет между открытиями окна.
    digest = sha1((label or "").encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big", signed=False)
