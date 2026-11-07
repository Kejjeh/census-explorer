"""Which places a view covers.

A scope is what the map, the table, the CSV, the brief and a share link all
cover. It is kept separate from the places a reader is inspecting or
comparing, which may lie outside it and are labelled when they do.

Three kinds, written as short codes so a scope survives a URL:

``nyc``
    New York City: its five documented counties, or the tracts in them. The
    membership is `composites.nyc`, never "whatever counties were built".
``nys``
    The whole study area: every county, or every tract, in the state. Only
    offered when the build is statewide.
``county:<GEOID>``
    The census tracts of one county. Tract level only. Membership is the
    first five digits of the tract GEOID, which the build checks against the
    boundary file's own STATEFP and COUNTYFP.

A request that names no scope is New York City. That is what every request
meant before the build covered the state, and a link or saved view made then
must not quietly widen to the whole state now.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

NYC = "nyc"
NYS = "nys"
COUNTY = "county"
DEFAULT = NYC

_COUNTY_RE = re.compile(r"^county:(\d{5})$")


class ScopeError(ValueError):
    """A scope that does not exist, or does not apply at this level."""


@dataclass(frozen=True)
class Scope:
    kind: str
    county: str | None = None

    @property
    def code(self) -> str:
        return f"{COUNTY}:{self.county}" if self.kind == COUNTY else self.kind


def parse(text: str | None) -> Scope:
    """Read a scope code. An absent one is New York City, never the state."""
    if text is None or text == "":
        return Scope(DEFAULT)
    if text in (NYC, NYS):
        return Scope(text)
    m = _COUNTY_RE.match(text)
    if m:
        return Scope(COUNTY, m.group(1))
    raise ScopeError(f"unknown scope {text!r}; use 'nyc', 'nys' or 'county:<GEOID>'")


def resolve(scope: Scope, level: str, areas: Iterable[dict],
            boroughs: Iterable[str], statewide: bool) -> list[str]:
    """The GEOIDs a scope covers at a level, in GEOID order.

    `areas` are the built areas as `{"geoid", "level"}` records. Nothing is
    inferred: New York City is exactly `boroughs`, a county is exactly the
    tracts whose GEOID begins with it, and the state is only available when
    the build says it covers the state.
    """
    at_level = sorted(a["geoid"] for a in areas if a["level"] == level)
    counties = {a["geoid"] for a in areas if a["level"] == "county"}
    boroughs = set(boroughs)

    if scope.kind == NYC:
        missing = sorted(boroughs - counties)
        # A statewide build that lacks a borough is broken, and a view called
        # New York City must not be drawn from it. A build that only ever
        # covered some configured counties (a fixture, a partial retrieval)
        # keeps its earlier behaviour: it lists what it has, is described as
        # partial, and the city reference refuses it on its own terms.
        if missing and statewide:
            raise ScopeError(
                "New York City cannot be shown: its documented counties "
                f"{', '.join(missing)} are not in this build")
        if level == "county":
            return [g for g in at_level if g in boroughs]
        return [g for g in at_level if g[:5] in boroughs]

    if scope.kind == NYS:
        if not statewide:
            raise ScopeError("this build does not cover the whole state")
        return at_level

    if scope.kind == COUNTY:
        if level != "tract":
            raise ScopeError(
                "a single-county scope lists that county's census tracts; "
                "switch to the tract level to use it")
        if scope.county not in counties:
            raise ScopeError(f"county {scope.county} is not in this build")
        return [g for g in at_level if g[:5] == scope.county]

    raise ScopeError(f"unknown scope kind {scope.kind!r}")


def describe(scope: Scope, level: str, count: int, county_name: str | None = None,
             partial: bool = False) -> str:
    """The scope in words, with its size, for every place it is shown.

    Census tracts are called census tracts, never neighbourhoods, and a
    county outside New York City is never called a borough.
    """
    n = f"{count:,}"
    if scope.kind == NYC and partial:
        noun = "counties" if level == "county" else "census tracts"
        return (f"{n} {noun} in the New York City counties this build has "
                "(not all five)")
    if level == "county":
        if scope.kind == NYC:
            return f"the {n} New York City boroughs"
        return f"all {n} counties in New York State"
    if scope.kind == NYC:
        return f"{n} census tracts in New York City"
    if scope.kind == NYS:
        return f"{n} census tracts in New York State"
    return f"{n} census tracts in {county_name or scope.county}"


def region_of(scope: Scope, boroughs: Iterable[str]) -> str:
    """The region a scope sits in: a county scope inherits its county's."""
    if scope.kind == COUNTY:
        return NYC if scope.county in set(boroughs) else NYS
    return scope.kind
