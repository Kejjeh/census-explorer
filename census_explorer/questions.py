"""Three starting questions, each mapped to measures the catalog already has.

A question is a deterministic route into the data, not an interpreter. Every
option it offers names an exact measure, an exact set of places and an exact
period that have already been validated against the published release. If a
question cannot be answered from what is built, it says so and offers nothing.

Nothing here guesses a metric, infers a topic from free text, or invents a
neighbourhood. A census tract is called a census tract.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

#: Question identifiers are part of the saved-brief format, so they are stable.
WHO_LIVES_HERE = "who_lives_here"
COMPARE_PLACES = "compare_places"
BIRTHPLACE_CONCENTRATION = "birthplace_concentration"


@dataclass(frozen=True)
class MeasureOption:
    """One correctly defined thing a question can show."""

    measure_id: str
    label: str              # plain language, no cell codes
    counts_what: str        # what a value counts
    out_of: str             # the denominator, in words; "" for a count
    unit: str

    def to_json(self) -> dict:
        return {
            "measure_id": self.measure_id, "label": self.label,
            "counts_what": self.counts_what, "out_of": self.out_of,
            "unit": self.unit,
        }


@dataclass(frozen=True)
class Question:
    question_id: str
    title: str                     # what the user picks
    subtitle: str
    level: str                     # the geography this question works at
    place_mode: str                # single | several | all
    place_prompt: str
    measure_prompt: str
    answers: str                   # what the brief will actually say
    not_answered: list[str]        # what it deliberately does not claim
    benchmark_prompt: str = ""

    def to_json(self) -> dict:
        return {
            "question_id": self.question_id, "title": self.title,
            "subtitle": self.subtitle, "level": self.level,
            "place_mode": self.place_mode, "place_prompt": self.place_prompt,
            "measure_prompt": self.measure_prompt, "answers": self.answers,
            "not_answered": list(self.not_answered),
            "benchmark_prompt": self.benchmark_prompt,
        }


QUESTIONS: dict[str, Question] = {
    WHO_LIVES_HERE: Question(
        question_id=WHO_LIVES_HERE,
        title="Who lives here?",
        subtitle="A profile of one place: how many people, where they were born, "
                 "and what share hold a degree.",
        level="county",
        place_mode="single",
        place_prompt="Which place?",
        measure_prompt="Lead with which measure?",
        answers="How many people live in the place, what share are foreign-born, "
                "what share were born in this state or another state, and what "
                "share of adults aged 25 and over hold a bachelor's degree or "
                "higher.",
        not_answered=[
            "It does not say when anyone arrived. Place of birth is a count of "
            "residents born somewhere, not a count of recent arrivals.",
            "It does not cover income, poverty, housing or rent. Those tables are "
            "not in this build.",
            "'Born in New York State' is not 'born in New York City'. The published "
            "table records the state.",
            "'Foreign-born' means not a U.S. citizen at birth, which is not the "
            "same as born outside the United States: someone born abroad to a "
            "U.S. citizen parent, or in Puerto Rico, is native-born.",
        ],
        benchmark_prompt="Compare against",
    ),
    COMPARE_PLACES: Question(
        question_id=COMPARE_PLACES,
        title="How do these places compare?",
        subtitle="One measure across several places in the same period, on one "
                 "shared scale.",
        level="county",
        place_mode="several",
        place_prompt="Which places?",
        measure_prompt="Compare them on",
        answers="The same measure for each selected place in one reference period, "
                "with each place's margin of error, ranked and drawn on a shared "
                "scale.",
        not_answered=[
            "It does not test whether a difference between two places is "
            "statistically significant. That needs a test this build does not "
            "perform, and overlapping geography would make it invalid anyway.",
            "It does not compare periods. Comparing across reference periods needs "
            "boundary equivalence that has not been established.",
            "It does not explain why places differ.",
        ],
        benchmark_prompt="Also show",
    ),
    BIRTHPLACE_CONCENTRATION: Question(
        question_id=BIRTHPLACE_CONCENTRATION,
        title="Where is this birthplace group concentrated?",
        subtitle="One birthplace group across every census tract in the city.",
        level="tract",
        place_mode="all",
        place_prompt="Across",
        measure_prompt="Which birthplace group, and counted how?",
        answers="For each census tract, how many residents were born in the chosen "
                "place, or what share of that tract's foreign-born residents were, "
                "with the tracts where the value is highest listed.",
        not_answered=[
            "Census tracts are statistical areas, not neighbourhoods. This build "
            "has no documented neighbourhood boundaries, so tracts are called "
            "tracts and are identified by their published number.",
            "A concentration is not a settlement history. It does not say when "
            "anyone arrived or where they lived before.",
            "Tract estimates carry large margins of error. Read the uncertainty "
            "column before quoting a single tract.",
        ],
        benchmark_prompt="Compare each tract against",
    ),
}


# --- Measure options per question ------------------------------------------
#
# Each entry names a measure_id the catalog defines. `available()` drops any
# that the built dataset does not actually carry at the question's level, so a
# question never offers something that cannot be drawn.

#: The Census Bureau's own wording, quoted rather than paraphrased. Someone
#: born abroad to a U.S. citizen parent is native, so "born outside the United
#: States" names a different population from "foreign born".
FOREIGN_BORN_COUNTS = ("residents who were not U.S. citizens at birth, including "
                       "those who have since naturalised")

_PROFILE_MEASURES = [
    MeasureOption("total_population", "Total population",
                  "everyone living in the place", "", "persons"),
    MeasureOption("foreign_born_share", "Foreign-born share of residents",
                  FOREIGN_BORN_COUNTS,
                  "all residents of the place", "percent"),
    MeasureOption("foreign_born_population", "Foreign-born residents (number)",
                  FOREIGN_BORN_COUNTS, "", "persons"),
    MeasureOption("native_born_outside_us_share",
                  "Native-born but born outside the United States, share of residents",
                  "residents who were U.S. citizens at birth but were born outside "
                  "the 50 states and D.C. — for example in Puerto Rico, or abroad "
                  "to a U.S. citizen parent",
                  "all residents of the place", "percent"),
    MeasureOption("born_in_state_of_residence_share",
                  "Share born in this state",
                  "residents born in the state they now live in",
                  "all residents of the place", "percent"),
    MeasureOption("born_other_us_state_share",
                  "Share born in another U.S. state",
                  "residents born in a different U.S. state",
                  "all residents of the place", "percent"),
    MeasureOption("naturalized_share_of_foreign_born",
                  "Naturalised share of foreign-born residents",
                  "foreign-born residents — people who were not U.S. citizens at "
                  "birth — who have since become U.S. citizens by naturalisation",
                  "all foreign-born residents of the place", "percent"),
    MeasureOption("bachelors_plus_share_all",
                  "Share of adults 25+ with a bachelor's degree or higher",
                  "adults aged 25 and over whose highest attainment is a "
                  "bachelor's, graduate or professional degree",
                  "all adults aged 25 and over in the place", "percent"),
    MeasureOption("black_alone_foreign_born_share",
                  "Foreign-born share of Black residents",
                  "residents reporting Black or African American alone who were "
                  "not U.S. citizens at birth, including those who have since "
                  "naturalised",
                  "all residents reporting Black or African American alone",
                  "percent"),
]

#: Birthplace groups, as (slug, plain name). The measure ids are derived, and
#: `available()` verifies each one exists before it is offered.
BIRTHPLACE_GROUPS = [
    ("dominican_republic", "the Dominican Republic"),
    ("china", "China (including Hong Kong and Taiwan)"),
    ("mexico", "Mexico"),
    ("jamaica", "Jamaica"),
    ("ecuador", "Ecuador"),
    ("guyana", "Guyana"),
    ("bangladesh", "Bangladesh"),
    ("india", "India"),
    ("haiti", "Haiti"),
    ("italy", "Italy"),
    ("poland", "Poland"),
    ("caribbean", "the Caribbean"),
    ("europe", "Europe"),
    ("asia", "Asia"),
    ("africa", "Africa"),
    ("latin_america", "Latin America"),
]


def _birthplace_options() -> list[MeasureOption]:
    out: list[MeasureOption] = []
    for slug, place in BIRTHPLACE_GROUPS:
        out.append(MeasureOption(
            f"fb_{slug}_share_of_foreign_born",
            f"Born in {place} — share of foreign-born residents",
            f"residents born in {place} who were not U.S. citizens at birth",
            "foreign-born residents of the area, excluding people born at sea",
            "percent"))
        out.append(MeasureOption(
            f"fb_{slug}_count",
            f"Born in {place} — number of residents",
            f"residents born in {place} who were not U.S. citizens at birth",
            "", "persons"))
    return out


QUESTION_MEASURES: dict[str, list[MeasureOption]] = {
    WHO_LIVES_HERE: _PROFILE_MEASURES,
    COMPARE_PLACES: _PROFILE_MEASURES + _birthplace_options(),
    BIRTHPLACE_CONCENTRATION: _birthplace_options(),
}


def available(question_id: str, dataset: dict[str, Any]) -> list[MeasureOption]:
    """The options this question can actually offer from a built dataset."""
    question = QUESTIONS[question_id]
    by_id = {m["measure_id"]: m for m in dataset.get("measures", [])}
    out = []
    for option in QUESTION_MEASURES[question_id]:
        entry = by_id.get(option.measure_id)
        if entry is None:
            continue
        if not entry.get("availability", {}).get(question.level, False):
            continue
        out.append(option)
    return out


def describe(question_id: str, option: MeasureOption, period_label: str,
             places: str) -> dict[str, str]:
    """The plain-language selection summary shown before any number.

    No cell codes: those belong in the source details.
    """
    question = QUESTIONS[question_id]
    counted = f"We are counting {option.counts_what}."
    if option.unit == "percent":
        out_of = f"Shown as a percentage of {option.out_of}."
        # The unit is the denominator. Saying "percent of residents" for a
        # share of the foreign-born, or of adults 25 and over, describes a
        # quantity the measure does not compute.
        unit = f"percent of {option.out_of}"
    else:
        out_of = ("Shown as a number of people, not a share, so it reflects how "
                  "large the place is as well as its composition.")
        unit = "people (a count, not a share)"
    return {
        "question": question.title,
        "places": places,
        "period": f"{period_label} — a five-year period estimate, not a single year",
        "measure": option.label,
        "counted": counted,
        "out_of": out_of,
        "unit": unit,
    }


def to_json(dataset: dict[str, Any]) -> list[dict]:
    """The whole question catalog, with only the options this build supports."""
    out = []
    for qid, question in QUESTIONS.items():
        options = available(qid, dataset)
        entry = question.to_json()
        entry["measures"] = [o.to_json() for o in options]
        entry["supported"] = bool(options)
        if not options:
            entry["unsupported_reason"] = (
                f"no measure this question needs is available at {question.level} "
                "level in the built dataset")
        out.append(entry)
    return out
