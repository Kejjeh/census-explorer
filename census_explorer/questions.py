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


#: Measures from the education and race tables. Each denominator below is the
#: measure's own denominator cell as the catalog defines it, not the source
#: table's published universe: B06009 publishes everything out of "Population
#: 25 years and over", but a share of foreign-born adults divides by the
#: foreign-born row of that table, and saying otherwise would name a quantity
#: the measure does not compute.
_EDUCATION_AND_RACE_MEASURES = [
    MeasureOption("adults_25_plus_population", "Adults aged 25 and over (number)",
                  "residents aged 25 and over", "", "persons"),
    MeasureOption("bachelors_plus_share_foreign_born",
                  "Foreign-born adults 25+ with a bachelor's degree or higher",
                  "adults aged 25 and over who were not U.S. citizens at birth "
                  "and whose highest attainment is a bachelor's, graduate or "
                  "professional degree",
                  "all foreign-born adults aged 25 and over in the place — "
                  "adults who were not U.S. citizens at birth",
                  "percent"),
    MeasureOption("bachelors_plus_share_born_in_state",
                  "Adults 25+ born in New York State with a bachelor's degree or higher",
                  "adults aged 25 and over born in New York State whose highest "
                  "attainment is a bachelor's, graduate or professional degree",
                  "all adults aged 25 and over born in New York State", "percent"),
    MeasureOption("bachelors_plus_share_born_other_state",
                  "Adults 25+ born in another U.S. state with a bachelor's degree or higher",
                  "adults aged 25 and over born in a different U.S. state whose "
                  "highest attainment is a bachelor's, graduate or professional degree",
                  "all adults aged 25 and over born in a different U.S. state",
                  "percent"),
    MeasureOption("black_alone_population",
                  "Residents reporting Black or African American alone (number)",
                  "residents reporting Black or African American alone", "",
                  "persons"),
    MeasureOption("black_alone_born_in_state_share",
                  "Black residents born in New York State, share",
                  "residents reporting Black or African American alone who were "
                  "born in New York State, which is not the same as born in New "
                  "York City",
                  "all residents reporting Black or African American alone",
                  "percent"),
]

#: Every measure the explorer can offer, in the order the sidebar groups them.
#: `COMPARE_PLACES` carries the whole list because that question places no
#: restriction on what is being compared; the other two stay narrower because
#: their wording and their `not_answered` lines are written for their topic.
_ALL_MEASURES = (_PROFILE_MEASURES + _EDUCATION_AND_RACE_MEASURES
                 + _birthplace_options())

QUESTION_MEASURES: dict[str, list[MeasureOption]] = {
    WHO_LIVES_HERE: _PROFILE_MEASURES,
    COMPARE_PLACES: _ALL_MEASURES,
    BIRTHPLACE_CONCENTRATION: _birthplace_options(),
}

#: Said of any tract-level selection, whichever question frames the brief.
#: These are properties of the geography, not of the question that happened to
#: be asked, so they must not disappear when a tract map is framed as a
#: comparison rather than as a birthplace concentration.
TRACT_CAVEATS = [
    "Census tracts are statistical areas, not neighbourhoods. This build has "
    "no documented neighbourhood boundaries, so tracts are called tracts and "
    "are identified by their published number.",
    "Tract estimates carry large margins of error. Read the uncertainty "
    "column before quoting a single tract.",
]

#: Group headings for the sidebar, keyed by the catalog's own `concept`. A
#: measure whose concept is not listed keeps its concept as the heading.
CONCEPT_GROUPS: dict[str, str] = {
    "Population size": "Population",
    "Nativity": "Born in the U.S. or abroad",
    "Citizenship": "Citizenship",
    "Place of birth of the foreign-born population":
        "Birthplace of foreign-born residents",
    "Educational attainment by place of birth": "Education by place of birth",
    "Place of birth by race": "Race and place of birth",
}

#: The order those groups appear in, broadest first.
CONCEPT_ORDER = [
    "Population size",
    "Nativity",
    "Citizenship",
    "Place of birth of the foreign-born population",
    "Education by place of birth",
    "Place of birth by race",
]


def base_limitations(question: Question, level: str) -> list[str]:
    """What a brief must say it does not claim, for this question at this level.

    The tract lines are properties of the geography rather than of the
    question, so they are added whichever question frames a tract brief, and
    never added twice.
    """
    out = list(question.not_answered)
    if level == "tract":
        for caveat in TRACT_CAVEATS:
            if caveat not in out:
                out.append(caveat)
    return out


def catalog(dataset: dict[str, Any], level: str) -> list[MeasureOption]:
    """Every option the built dataset carries at `level`, in sidebar order.

    Unlike `available()`, this is not scoped to one question: it is what the
    explorer offers. It still never invents a measure — an option is dropped
    unless the build records it as available at that exact level.
    """
    by_id = {m["measure_id"]: m for m in dataset.get("measures", [])}
    out: list[MeasureOption] = []
    seen: set[str] = set()
    for option in _ALL_MEASURES:
        if option.measure_id in seen:
            continue
        entry = by_id.get(option.measure_id)
        if entry is None:
            continue
        if not entry.get("availability", {}).get(level, False):
            continue
        seen.add(option.measure_id)
        out.append(option)
    return out


def question_for(measure_id: str, level: str, area_count: int) -> str:
    """The question whose wording fits this selection and carries this measure.

    A brief is always framed by one of the three questions, so the explorer
    must resolve one for whatever the user selected. The resolved question is
    guaranteed to list `measure_id`, which is what `brief_context` requires.
    """
    birthplace = {o.measure_id for o in _birthplace_options()}
    profile = {o.measure_id for o in _PROFILE_MEASURES}
    if level == "tract" and measure_id in birthplace:
        return BIRTHPLACE_CONCENTRATION
    if level == "county" and area_count == 1 and measure_id in profile:
        return WHO_LIVES_HERE
    if measure_id in {o.measure_id for o in _ALL_MEASURES}:
        return COMPARE_PLACES
    raise ValueError(f"no question offers the measure '{measure_id}'")


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
        # The denominator on its own, so a caption can name it separately from
        # the published universe of the source table. They are different
        # things and a naturalisation share out of "Total population" reads as
        # a contradiction.
        "denominator_phrase": option.out_of if option.unit == "percent" else "",
    }


def describe_contents(option: MeasureOption, place_phrase: str, area_count: int,
                      level: str, period_label: str,
                      benchmark_label: str | None = None) -> str:
    """What this brief contains, derived from the selection that built it.

    A question's own description says what that question is capable of
    answering. A brief that contains one measure must not borrow it: a reader
    holding a single-measure brief should not be told it covers four.
    """
    if area_count == 1:
        scope = f"for {place_phrase}"
    else:
        noun = "census tracts" if level == "tract" else "boroughs"
        scope = f"for {area_count:,} {noun}"
    sentence = (f"This brief reports one measure, {option.label}, {scope}, "
                f"over {period_label}.")
    if benchmark_label:
        sentence += f" It also shows {benchmark_label} as a reference value."
    return sentence


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
