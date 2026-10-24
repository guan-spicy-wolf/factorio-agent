"""Pipe routing (filled in M3)."""

from __future__ import annotations


def route_links(site, gd, grid, conn) -> None:
    for ln in site.links:
        if site.interface(ln.src).kind == "fluid":
            raise NotImplementedError("pipe routing")
